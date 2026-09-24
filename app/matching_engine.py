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
from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import Session, select

from .models import Shipment, ShipmentRequirement, ScanEvent, SquishyType
from .timeutil import utc_now


@dataclass
class RemainingRequirement:
    name: str
    quantity_remaining: int


@dataclass
class ShipmentItem:
    name: str
    quantity: int


@dataclass
class ScanResult:
    matched: bool
    shipment_id: Optional[int] = None
    tracking_number: Optional[str] = None
    bin_number: Optional[int] = None
    shipment_complete: bool = False
    pdf_label_page_index: Optional[int] = None
    message: str = ""
    # Structured version of the "still needs" list in `message` -- squishy
    # type names come through verbatim (never translated), so the frontend
    # can build a localized sentence around them instead of parsing English
    # out of `message`. Only populated for the "in_progress" case.
    remaining: list[RemainingRequirement] = field(default_factory=list)
    # Every item that made up the shipment, for the frontend's translated
    # completion message (a bundle vs. a single-item order reads bin_number
    # to tell which wording to use). Only populated for the "complete" case.
    items: list[ShipmentItem] = field(default_factory=list)


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

    # Check if every requirement on this shipment is now satisfied.
    all_requirements = session.exec(
        select(ShipmentRequirement).where(ShipmentRequirement.shipment_id == shipment.id)
    ).all()
    shipment_complete = all(r.quantity_scanned >= r.quantity_required for r in all_requirements)

    if shipment_complete:
        shipment.is_complete = True
        shipment.completed_at = utc_now()
    elif shipment.bin_number is None:
        # Only assign a bin when the shipment is genuinely going to sit and
        # wait for more scans -- a single-item order that completes on this
        # same scan never physically occupies one, so it must stay None
        # (frontend uses that null to tell a bundle-with-a-bin apart from
        # an instant single-item completion).
        shipment.bin_number = _next_available_bin(session, wall_set_id)

    session.add(shipment)
    session.add(ScanEvent(
        wall_set_id=wall_set_id,
        squishy_type_id=squishy_type_id,
        shipment_id=shipment.id,
        matched=True,
    ))
    session.commit()

    name_by_type_id = {
        t.id: t.name for t in session.exec(
            select(SquishyType).where(
                SquishyType.id.in_([r.squishy_type_id for r in all_requirements])
            )
        ).all()
    }

    if shipment_complete:
        items = [
            ShipmentItem(
                name=name_by_type_id.get(r.squishy_type_id, str(r.squishy_type_id)),
                quantity=r.quantity_required,
            )
            for r in all_requirements
        ]
        return ScanResult(
            matched=True,
            shipment_id=shipment.id,
            tracking_number=shipment.tracking_number,
            bin_number=shipment.bin_number,
            shipment_complete=True,
            pdf_label_page_index=shipment.pdf_label_page_index,
            items=items,
            message="Shipment complete, print label.",
        )
    else:
        still_needed = [r for r in all_requirements if r.quantity_scanned < r.quantity_required]
        remaining_structured = [
            RemainingRequirement(
                name=name_by_type_id.get(r.squishy_type_id, str(r.squishy_type_id)),
                quantity_remaining=r.quantity_required - r.quantity_scanned,
            )
            for r in still_needed
        ]
        remaining = [
            f"{r.name}: {r.quantity_remaining} more" for r in remaining_structured
        ]
        return ScanResult(
            matched=True,
            shipment_id=shipment.id,
            tracking_number=shipment.tracking_number,
            bin_number=shipment.bin_number,
            shipment_complete=False,
            remaining=remaining_structured,
            message=f"Goes in bin {shipment.bin_number}. Still needs: {', '.join(remaining)}",
        )
