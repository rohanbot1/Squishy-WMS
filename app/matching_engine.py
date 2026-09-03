"""
The core packer-facing logic. One function, one job: given a scanned
squishy type, figure out which open shipment it belongs to and whether
that shipment is now complete.

This deliberately does NOT special-case "single item" vs "bundle" orders.
Every shipment is just a requirement list (squishy_type -> qty needed).
A single-item order is the case where that list has one entry with qty 1,
so it completes on the first scan. A bundle is the case where it takes
several scans across different types (or the same type more than once,
e.g. an order for 2x the same squishy) to zero out the list. Same code
path either way.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from .models import Shipment, ShipmentRequirement, ScanEvent, SquishyType


@dataclass
class ScanResult:
    matched: bool
    shipment_id: Optional[int] = None
    tracking_number: Optional[str] = None
    bin_number: Optional[int] = None
    shipment_complete: bool = False
    pdf_label_page_index: Optional[int] = None
    message: str = ""


def _next_available_bin(session: Session, wall_set_id: int) -> int:
    """Bins get reused once a shipment ships. Picks the lowest free bin
    number currently not tied to an open (incomplete) shipment."""
    in_use = session.exec(
        select(Shipment.bin_number).where(
            Shipment.wall_set_id == wall_set_id,
            Shipment.is_complete == False,  # noqa: E712
            Shipment.bin_number != None,  # noqa: E711
        )
    ).all()
    in_use_set = set(in_use)
    bin_number = 1
    while bin_number in in_use_set:
        bin_number += 1
    return bin_number


def scan_item(session: Session, wall_set_id: int, squishy_type_id: int) -> ScanResult:
    # Find open requirements for this squishy type, oldest shipment first.
    open_requirements = session.exec(
        select(ShipmentRequirement, Shipment)
        .join(Shipment, ShipmentRequirement.shipment_id == Shipment.id)
        .where(
            Shipment.wall_set_id == wall_set_id,
            Shipment.is_complete == False,  # noqa: E712
            ShipmentRequirement.squishy_type_id == squishy_type_id,
        )
        .order_by(Shipment.created_at)
    ).all()

    target = None
    for requirement, shipment in open_requirements:
        if requirement.quantity_scanned < requirement.quantity_required:
            target = (requirement, shipment)
            break

    if target is None:
        session.add(ScanEvent(
            wall_set_id=wall_set_id,
            squishy_type_id=squishy_type_id,
            matched=False,
        ))
        session.commit()
        return ScanResult(matched=False, message="No open shipment needs this item right now.")

    requirement, shipment = target
    requirement.quantity_scanned += 1
    session.add(requirement)

    if shipment.bin_number is None:
        shipment.bin_number = _next_available_bin(session, wall_set_id)

    # Check if every requirement on this shipment is now satisfied.
    all_requirements = session.exec(
        select(ShipmentRequirement).where(ShipmentRequirement.shipment_id == shipment.id)
    ).all()
    shipment_complete = all(r.quantity_scanned >= r.quantity_required for r in all_requirements)

    if shipment_complete:
        shipment.is_complete = True
        shipment.completed_at = datetime.utcnow()

    session.add(shipment)
    session.add(ScanEvent(
        wall_set_id=wall_set_id,
        squishy_type_id=squishy_type_id,
        shipment_id=shipment.id,
        matched=True,
    ))
    session.commit()

    if shipment_complete:
        return ScanResult(
            matched=True,
            shipment_id=shipment.id,
            tracking_number=shipment.tracking_number,
            bin_number=shipment.bin_number,
            shipment_complete=True,
            pdf_label_page_index=shipment.pdf_label_page_index,
            message="Shipment complete, print label.",
        )
    else:
        still_needed = [r for r in all_requirements if r.quantity_scanned < r.quantity_required]
        name_by_type_id = {
            t.id: t.name for t in session.exec(
                select(SquishyType).where(
                    SquishyType.id.in_([r.squishy_type_id for r in still_needed])
                )
            ).all()
        }
        remaining = [
            f"{name_by_type_id.get(r.squishy_type_id, r.squishy_type_id)}: "
            f"{r.quantity_required - r.quantity_scanned} more"
            for r in still_needed
        ]
        return ScanResult(
            matched=True,
            shipment_id=shipment.id,
            tracking_number=shipment.tracking_number,
            bin_number=shipment.bin_number,
            shipment_complete=False,
            message=f"Goes in bin {shipment.bin_number}. Still needs: {', '.join(remaining)}",
        )
