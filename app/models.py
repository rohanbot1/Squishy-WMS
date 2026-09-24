"""
Core data models for the squishy WMS.

Key design decision: shipments are keyed by TikTok's tracking number, not
order ID. TikTok combines multiple separate orders into one physical box
before we ever see the data, and the tracking number is the field that's
shared across every line that ends up in the same box. Grouping by order ID
would miss that combination entirely.
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import DateTime
from sqlmodel import SQLModel, Field, Relationship

from .timeutil import utc_now


class SquishyType(SQLModel, table=True):
    """One row per distinct squishy product (e.g. 'Yellow Butter', 'Sugar Baby').

    name is stored exactly as it appears in the TikTok listing, emojis
    included, since that's the string employees type in and the string
    that shows up (uncorrupted) in TikTok's CSV exports.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    internal_code: str = Field(unique=True)  # what actually gets encoded in the barcode
    is_giveaway_item: bool = Field(default=False)  # e.g. the recurring butter giveaway
    # Soft-delete flag, not a real delete -- types are referenced by
    # historical Shipments, ScanEvents, and FinancialRecords that all need
    # to keep displaying correctly forever. Deactivating only removes a
    # type from the catalog/wall-builder picker; the name stays reserved
    # (still unique) so a type can't be "recreated" as a distinct row --
    # see the reactivate-on-duplicate-name handling in app/api.py.
    active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utc_now)

    @staticmethod
    def normalize(name: str) -> str:
        """Strip whitespace/case for fuzzy matching fallback. Emoji are NOT
        stripped here on purpose -- the primary match is exact, this is only
        a fallback normalization used if an exact match fails."""
        return " ".join(name.split()).strip().lower()


class WallSet(SQLModel, table=True):
    """One physical wall / one livestream's worth of pre-built inventory."""
    id: Optional[int] = Field(default=None, primary_key=True)
    label: str  # e.g. "9/1 stream" or whatever Binit's team calls it
    created_at: datetime = Field(default_factory=utc_now)
    orders_uploaded: bool = Field(default=False)
    pdf_file_path: Optional[str] = None  # storage key for the master label/packing-slip PDF (app/storage.py), set on upload


class WallSetItem(SQLModel, table=True):
    """How many of each squishy type were pre-built into a given wall."""
    id: Optional[int] = Field(default=None, primary_key=True)
    wall_set_id: int = Field(foreign_key="wallset.id")
    squishy_type_id: int = Field(foreign_key="squishytype.id")
    quantity: int


class Shipment(SQLModel, table=True):
    """One box / one label. Keyed by TikTok tracking number.

    pdf_page_index points at the label-image page (the even-numbered page
    in TikTok's export) inside the master PDF for this wall set, so once
    a shipment is complete we know exactly which page to print.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    wall_set_id: int = Field(foreign_key="wallset.id")
    tracking_number: str = Field(index=True)
    order_ids: str  # comma-separated, since a shipment can bundle several
    pdf_label_page_index: Optional[int] = None
    bin_number: Optional[int] = None  # assigned on first partial scan
    is_complete: bool = Field(default=False)
    completed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)


class ShipmentRequirement(SQLModel, table=True):
    """How many of a given squishy type a shipment needs, and how many
    have been scanned so far. This is the row the matching engine
    decrements on every scan."""
    id: Optional[int] = Field(default=None, primary_key=True)
    shipment_id: int = Field(foreign_key="shipment.id")
    squishy_type_id: int = Field(foreign_key="squishytype.id")
    quantity_required: int
    quantity_scanned: int = Field(default=0)


class ScanEvent(SQLModel, table=True):
    """Audit log of every scan, for debugging / reconciliation."""
    id: Optional[int] = Field(default=None, primary_key=True)
    wall_set_id: int = Field(foreign_key="wallset.id")
    squishy_type_id: int = Field(foreign_key="squishytype.id")
    shipment_id: Optional[int] = Field(default=None, foreign_key="shipment.id")
    matched: bool  # False if scanned but no open shipment needed it
    scanned_at: datetime = Field(default_factory=utc_now)


class AdminSession(SQLModel, table=True):
    """A logged-in admin session. There's exactly one admin (Binit) -- this
    table just needs to survive server restarts and let a cookie prove
    "this request is Binit" to the require_admin dependency (app/auth.py)."""
    token: str = Field(primary_key=True)  # secrets.token_urlsafe(32)
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime


class FloorSession(SQLModel, table=True):
    """A device that's entered the shared floor PIN -- gates Wall Builder,
    Packer Scan, and Shipments (see require_floor_access in app/auth.py).
    Deliberately not unified with AdminSession: different secret strength,
    different session length, and floor-PIN attempts get rate-limited in a
    way admin login doesn't, so keeping them as two small independent
    things stays clearer than one generalized one."""
    token: str = Field(primary_key=True)  # secrets.token_urlsafe(32)
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime


class PinAttempt(SQLModel, table=True):
    """Exponential-backoff state for one source IP's floor-PIN attempts.
    Never a hard lockout -- see verify_floor_pin in app/auth.py -- just an
    increasing delay so brute-forcing all 10,000 combinations of a 4-digit
    PIN over the public internet takes days, without ever fully blocking a
    legitimate floor worker."""
    ip_address: str = Field(primary_key=True)
    failure_count: int = Field(default=0)
    last_attempt_at: datetime = Field(default_factory=utc_now)


class FinancialRecord(SQLModel, table=True):
    """One stream's financial/performance numbers -- Binit's admin-only view
    on data that otherwise only lives in his manual tracking spreadsheet.
    One record per WallSet (1:1); item quantities are NOT duplicated here,
    they're read live from that wall set's WallSetItem rows and joined
    against FinancialRecordItemCost. Runs alongside the spreadsheet, not a
    replacement for it."""
    id: Optional[int] = Field(default=None, primary_key=True)
    wall_set_id: int = Field(foreign_key="wallset.id", unique=True)
    streamer: str
    # Wall-clock times as typed into a datetime-local input -- no zone, and
    # deliberately stored naive exactly as entered (plain TIMESTAMP), not
    # converted to UTC like every other timestamp here. Explicit sa_type so
    # SQLModel's UTC column type (which rejects naive values) isn't applied.
    stream_started_at: datetime = Field(sa_type=DateTime(timezone=False))
    stream_ended_at: datetime = Field(sa_type=DateTime(timezone=False))
    revenue: float
    fees: float
    bid_average: float
    giveaway_squishy_type_id: Optional[int] = Field(default=None, foreign_key="squishytype.id")
    giveaway_quantity: Optional[int] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class FinancialRecordItemCost(SQLModel, table=True):
    """Per-unit cost for one squishy type, snapshotted at record-save time --
    costs drift stream to stream, so this is deliberately not a permanent
    field on SquishyType. Quantity comes from WallSetItem, not from here."""
    id: Optional[int] = Field(default=None, primary_key=True)
    financial_record_id: int = Field(foreign_key="financialrecord.id")
    squishy_type_id: int = Field(foreign_key="squishytype.id")
    unit_cost: float
