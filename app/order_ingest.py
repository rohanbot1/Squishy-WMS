"""
Order ingestion: turns TikTok's raw CSV export into Shipment +
ShipmentRequirement rows, and figures out which page of the master PDF
holds each shipment's printable label.

Deliberately does NOT read item names or quantities from the PDF. The PDF's
text layer mangles emoji into null bytes (confirmed with two different
extraction libraries on Binit's real export), while the CSV's Product Name
column keeps them intact. The PDF is only ever used for its label page
images, addressed by tracking number.
"""
import csv
import io
from collections import defaultdict
from typing import Optional

import fitz  # PyMuPDF
from sqlalchemy import case
from sqlmodel import Session, select, update

from .models import SquishyType, Shipment, ShipmentRequirement


def _build_squishy_type_resolver(session: Session):
    """Fetches every SquishyType once (a single round trip) and returns a
    lookup function equivalent to the old per-call _find_squishy_type:
    exact match first, falling back to a whitespace/case-normalized match
    so small formatting drift doesn't silently drop an item.

    This existed as a live query per product name -- against a real
    network-latency database (Postgres, not SQLite), that meant one
    round trip per requirement line (hundreds, for a real order export)
    on top of the per-shipment round trips below. Resolving everything
    from one in-memory snapshot instead is what actually fixes that,
    not just moving the same query somewhere that looks tidier.
    """
    all_types = session.exec(select(SquishyType)).all()
    by_exact_name = {t.name: t for t in all_types}  # SquishyType.name is unique -- no collision risk
    # setdefault, not a dict comprehension: preserves "first match in
    # query order wins" on a normalized-name collision, matching the
    # original loop's semantics exactly rather than silently flipping to
    # "last one wins".
    by_normalized_name: dict[str, SquishyType] = {}
    for t in all_types:
        by_normalized_name.setdefault(SquishyType.normalize(t.name), t)

    def resolve(product_name: str) -> Optional[SquishyType]:
        exact = by_exact_name.get(product_name)
        if exact is not None:
            return exact
        return by_normalized_name.get(SquishyType.normalize(product_name))

    return resolve


def parse_csv_into_shipments(csv_bytes: bytes, wall_set_id: int, session: Session) -> dict:
    """Groups CSV rows by Tracking ID and creates one Shipment + its
    ShipmentRequirement rows per group.

    Returns a summary dict for reporting: counts of shipments created,
    line items matched, and any product names that didn't match a known
    SquishyType (so the wall builder can be fixed before packing starts).

    Batched into two phases (all shipments, then all requirements) with
    one flush apiece, instead of the original per-shipment flush plus
    per-requirement-line squishy-type query -- against SQLite that was
    544 round trips at effectively zero latency each; against a real
    hosted Postgres it was 544 round trips at real network latency each,
    which is what actually turned a near-instant local upload into a
    multi-minute one. SQLAlchemy 2.0's insertmanyvalues collapses each
    phase's add-many-then-flush-once into a small constant number of
    round trips regardless of row count, on both dialects.
    """
    # tracking_number -> {"order_ids": set(), "items": {product_name: qty}}
    groups: dict[str, dict] = defaultdict(lambda: {"order_ids": set(), "items": defaultdict(int)})

    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig")))
    reader.fieldnames = [h.strip() for h in reader.fieldnames]
    for row in reader:
        row = {k.strip(): (v.strip() if v else v) for k, v in row.items()}
        tracking = row.get("Tracking ID", "")
        if not tracking:
            continue
        order_id = row.get("Order ID", "")
        product_name = row.get("Product Name", "")
        qty = int(row.get("Quantity", "0") or 0)

        groups[tracking]["order_ids"].add(order_id)
        groups[tracking]["items"][product_name] += qty

    resolve_squishy_type = _build_squishy_type_resolver(session)

    # Phase 1: every Shipment, one flush.
    shipments_by_tracking: dict[str, Shipment] = {}
    for tracking_number, data in groups.items():
        shipment = Shipment(
            wall_set_id=wall_set_id,
            tracking_number=tracking_number,
            order_ids=",".join(sorted(data["order_ids"])),
        )
        session.add(shipment)
        shipments_by_tracking[tracking_number] = shipment
    session.flush()  # populates shipment.id on every pending shipment above

    # Phase 2: every ShipmentRequirement, one flush -- shipment.id is now
    # available on every shipment from phase 1's flush.
    unmatched_products: set[str] = set()
    requirements_created = 0
    for tracking_number, data in groups.items():
        shipment = shipments_by_tracking[tracking_number]
        for product_name, qty in data["items"].items():
            squishy_type = resolve_squishy_type(product_name)
            if squishy_type is None:
                unmatched_products.add(product_name)
                continue
            session.add(ShipmentRequirement(
                shipment_id=shipment.id,
                squishy_type_id=squishy_type.id,
                quantity_required=qty,
            ))
            requirements_created += 1

    session.commit()

    return {
        "shipments_created": len(groups),
        "requirements_created": requirements_created,
        "unmatched_products": sorted(unmatched_products),
    }


def index_label_pages_by_tracking(pdf_bytes: bytes) -> dict:
    """TikTok's export alternates: even page index = label image (no text
    layer), odd page index = packing slip text (which does have a clean,
    extractable tracking number since it's all digits, no emoji involved).

    Returns {tracking_number: label_page_index}.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    tracking_to_label_page: dict[str, int] = {}

    for page_index in range(len(doc)):
        text = doc[page_index].get_text()
        if "Tracking number:" not in text:
            continue
        # tracking number is split across a line break in the raw text
        # extraction (e.g. "923469039206671058659\n2"), so pull all
        # digit runs after the label and concatenate them.
        after = text.split("Tracking number:", 1)[1]
        digits = "".join(ch for ch in after[:40] if ch.isdigit())
        if digits:
            label_page_index = page_index - 1  # the preceding page is the label image
            tracking_to_label_page[digits] = label_page_index

    doc.close()
    return tracking_to_label_page


def attach_label_pages(session: Session, wall_set_id: int, pdf_bytes: bytes) -> int:
    """Fills in Shipment.pdf_label_page_index for every shipment in this
    wall set. Returns how many shipments got matched to a page.

    Written as a single CASE-expression UPDATE instead of one
    session.add()-per-shipment plus commit. SQLAlchemy's insertmanyvalues
    batching (what fixed parse_csv_into_shipments) only applies to
    INSERT -- an ORM attribute change still emits one UPDATE per dirty
    row at flush time regardless of API used (confirmed against the real
    Postgres instance: 146 individual UPDATEs took ~15s either way, attribute
    assignment or session.execute(update(...), list_of_mappings)). A single
    UPDATE ... SET col = CASE id WHEN ... END is one round trip regardless
    of row count, and is plain ANSI SQL -- no dialect-specific syntax, so
    it behaves the same on SQLite and Postgres.
    """
    tracking_to_page = index_label_pages_by_tracking(pdf_bytes)
    shipments = session.exec(
        select(Shipment.id, Shipment.tracking_number).where(Shipment.wall_set_id == wall_set_id)
    ).all()

    id_to_page = {
        shipment_id: tracking_to_page[tracking_number]
        for shipment_id, tracking_number in shipments
        if tracking_number in tracking_to_page
    }
    if not id_to_page:
        return 0

    session.execute(
        update(Shipment)
        .where(Shipment.id.in_(id_to_page.keys()))
        .values(pdf_label_page_index=case(*[(Shipment.id == sid, page) for sid, page in id_to_page.items()]))
    )
    session.commit()
    return len(id_to_page)
