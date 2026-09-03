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
from collections import defaultdict
from typing import Optional

import fitz  # PyMuPDF
from sqlmodel import Session, select

from .models import SquishyType, Shipment, ShipmentRequirement


def _find_squishy_type(session: Session, product_name: str) -> Optional[SquishyType]:
    """Exact match first (this should be the common case, since the wall
    builder is filled in with the same string as the listing). Falls back
    to a whitespace/case-normalized match so small formatting drift
    doesn't silently drop an item."""
    exact = session.exec(
        select(SquishyType).where(SquishyType.name == product_name)
    ).first()
    if exact:
        return exact

    normalized_target = SquishyType.normalize(product_name)
    for candidate in session.exec(select(SquishyType)).all():
        if SquishyType.normalize(candidate.name) == normalized_target:
            return candidate
    return None


def parse_csv_into_shipments(csv_path: str, wall_set_id: int, session: Session) -> dict:
    """Groups CSV rows by Tracking ID and creates one Shipment + its
    ShipmentRequirement rows per group.

    Returns a summary dict for reporting: counts of shipments created,
    line items matched, and any product names that didn't match a known
    SquishyType (so the wall builder can be fixed before packing starts).
    """
    # tracking_number -> {"order_ids": set(), "items": {product_name: qty}}
    groups: dict[str, dict] = defaultdict(lambda: {"order_ids": set(), "items": defaultdict(int)})

    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
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

    unmatched_products: set[str] = set()
    shipments_created = 0
    requirements_created = 0

    for tracking_number, data in groups.items():
        shipment = Shipment(
            wall_set_id=wall_set_id,
            tracking_number=tracking_number,
            order_ids=",".join(sorted(data["order_ids"])),
        )
        session.add(shipment)
        session.flush()  # get shipment.id before adding requirements
        shipments_created += 1

        for product_name, qty in data["items"].items():
            squishy_type = _find_squishy_type(session, product_name)
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
        "shipments_created": shipments_created,
        "requirements_created": requirements_created,
        "unmatched_products": sorted(unmatched_products),
    }


def index_label_pages_by_tracking(pdf_path: str) -> dict:
    """TikTok's export alternates: even page index = label image (no text
    layer), odd page index = packing slip text (which does have a clean,
    extractable tracking number since it's all digits, no emoji involved).

    Returns {tracking_number: label_page_index}.
    """
    doc = fitz.open(pdf_path)
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


def attach_label_pages(session: Session, wall_set_id: int, pdf_path: str) -> int:
    """Fills in Shipment.pdf_label_page_index for every shipment in this
    wall set. Returns how many shipments got matched to a page."""
    tracking_to_page = index_label_pages_by_tracking(pdf_path)
    shipments = session.exec(
        select(Shipment).where(Shipment.wall_set_id == wall_set_id)
    ).all()

    matched = 0
    for shipment in shipments:
        page = tracking_to_page.get(shipment.tracking_number)
        if page is not None:
            shipment.pdf_label_page_index = page
            session.add(shipment)
            matched += 1

    session.commit()
    return matched
