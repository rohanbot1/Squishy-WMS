"""
Proves the core engine against Binit's actual TikTok export, not synthetic
data. Seeds the catalog from the CSV's own unique product names (standing
in for "the wall was already built with these types"), ingests the real
CSV + PDF, then replays every required scan and checks that every
shipment completes exactly once and gets a real label page attached.
"""
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlmodel import SQLModel, Session, create_engine, select

from app.models import SquishyType, WallSet, Shipment, ShipmentRequirement
from app.order_ingest import parse_csv_into_shipments, attach_label_pages
from app.matching_engine import scan_item

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "sample_data", "To_Ship_order-2026-09-01-17_51.csv")
PDF_PATH = os.path.join(os.path.dirname(__file__), "..", "sample_data", "09-01_14-50-39_Shipping_label_Packing_slip.pdf")

engine = create_engine("sqlite:///:memory:")
SQLModel.metadata.create_all(engine)


def seed_catalog_from_csv(session: Session, csv_path: str) -> None:
    names = set()
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [h.strip() for h in reader.fieldnames]
        for row in reader:
            name = (row.get("Product Name") or "").strip()
            if name:
                names.add(name)

    for i, name in enumerate(sorted(names)):
        session.add(SquishyType(
            name=name,
            internal_code=f"SQ{i:04d}",
            is_giveaway_item=(name.strip().upper() == "BUTTER SQUISHY"),
        ))
    session.commit()


with Session(engine) as session:
    seed_catalog_from_csv(session, CSV_PATH)
    catalog_size = len(session.exec(select(SquishyType)).all())
    print(f"Catalog seeded: {catalog_size} distinct squishy types")

    wall_set = WallSet(label="9/1 test set")
    session.add(wall_set)
    session.commit()
    session.refresh(wall_set)

    with open(CSV_PATH, "rb") as f:
        csv_bytes = f.read()
    with open(PDF_PATH, "rb") as f:
        pdf_bytes = f.read()

    ingest_summary = parse_csv_into_shipments(csv_bytes, wall_set.id, session)
    print(f"Shipments created: {ingest_summary['shipments_created']}")
    print(f"Requirement lines created: {ingest_summary['requirements_created']}")
    print(f"Unmatched product names: {ingest_summary['unmatched_products']}")

    labels_matched = attach_label_pages(session, wall_set.id, pdf_bytes)
    total_shipments = len(session.exec(
        select(Shipment).where(Shipment.wall_set_id == wall_set.id)
    ).all())
    print(f"Label pages matched: {labels_matched} / {total_shipments} shipments")

    # Report bundle sizes so we can sanity-check against what we found earlier
    multi_item_shipments = 0
    for shipment in session.exec(select(Shipment).where(Shipment.wall_set_id == wall_set.id)).all():
        reqs = session.exec(select(ShipmentRequirement).where(ShipmentRequirement.shipment_id == shipment.id)).all()
        if len(reqs) > 1:
            multi_item_shipments += 1
    print(f"Multi-item (bundled) shipments: {multi_item_shipments}")

    # --- Simulate the packing floor: replay every required scan ---
    scan_queue = []
    for req in session.exec(select(ShipmentRequirement)).all():
        scan_queue.extend([req.squishy_type_id] * (req.quantity_required - req.quantity_scanned))

    import random
    random.seed(42)
    random.shuffle(scan_queue)  # packer doesn't scan in tidy order in real life

    completed_labels = []
    no_match_scans = 0
    for squishy_type_id in scan_queue:
        result = scan_item(session, wall_set.id, squishy_type_id)
        if not result.matched:
            no_match_scans += 1
        elif result.shipment_complete:
            completed_labels.append((result.tracking_number, result.pdf_label_page_index))

    print(f"\nTotal scans replayed: {len(scan_queue)}")
    print(f"Scans with no matching open shipment: {no_match_scans}")
    print(f"Shipments completed via scanning: {len(completed_labels)}")
    print(f"Shipments completed with a valid label page attached: "
          f"{sum(1 for _, page in completed_labels if page is not None)}")

    still_open = session.exec(
        select(Shipment).where(Shipment.wall_set_id == wall_set.id, Shipment.is_complete == False)
    ).all()
    print(f"Shipments still open after all scans replayed: {len(still_open)}")

    print("\nSample completed shipment label pages (tracking -> pdf page index):")
    for tracking, page in completed_labels[:5]:
        print(f"  {tracking} -> page {page}")
