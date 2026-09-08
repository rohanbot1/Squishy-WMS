"""
One-off script: copies every row of real business data from the local
SQLite database into a target Postgres database (e.g. Render's managed
Postgres, via its DATABASE_URL), preserving primary keys, then resets
Postgres's auto-increment sequences past the migrated IDs so the next row
created after migration doesn't collide with one that was just copied in.

Deliberately does NOT migrate AdminSession, FloorSession, or PinAttempt --
those are live login/rate-limit state tied to cookies already sitting in
someone's browser and an IP's recent attempt history, not business data.
A fresh deployment starting with zero sessions and a clean rate-limit
slate is the correct, safer behavior, not something to carry over.

Not something normal deployment ever runs automatically -- this is a
one-time step for moving an existing local SQLite database (built up from
local/LAN use) into a fresh Postgres-backed deployment. Refuses to run if
the target already has any rows in it, since that almost certainly means
"you're about to duplicate data" rather than "this is genuinely empty."

Usage:
    DATABASE_URL=postgresql://... python scripts/migrate_sqlite_to_postgres.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from sqlmodel import SQLModel, Session, create_engine, select

from app.models import (
    FinancialRecord, FinancialRecordItemCost, ScanEvent, Shipment,
    ShipmentRequirement, SquishyType, WallSet, WallSetItem,
)

SQLITE_PATH = os.path.join(os.path.dirname(__file__), "..", "squishy_wms.db")

# FK-safe order: every table only references ones already migrated above it.
TABLES_IN_ORDER = [
    SquishyType, WallSet, WallSetItem, Shipment, ShipmentRequirement,
    ScanEvent, FinancialRecord, FinancialRecordItemCost,
]


def main() -> None:
    target_url = os.environ.get("DATABASE_URL")
    if not target_url:
        print("Set DATABASE_URL to the target Postgres connection string first.")
        sys.exit(1)
    if target_url.startswith("postgres://"):
        target_url = target_url.replace("postgres://", "postgresql://", 1)

    if not os.path.exists(SQLITE_PATH):
        print(f"No local database found at {os.path.abspath(SQLITE_PATH)}.")
        sys.exit(1)

    source_engine = create_engine(f"sqlite:///{SQLITE_PATH}")
    target_engine = create_engine(target_url)

    SQLModel.metadata.create_all(target_engine)

    with Session(target_engine) as target_session:
        for model in TABLES_IN_ORDER:
            if target_session.exec(select(model)).first() is not None:
                print(
                    f"ABORT: target already has rows in {model.__tablename__} -- "
                    "refusing to risk duplicating data. Migrate into a genuinely "
                    "empty database."
                )
                sys.exit(1)

    total_copied = 0
    with Session(source_engine) as source_session, Session(target_engine) as target_session:
        for model in TABLES_IN_ORDER:
            rows = source_session.exec(select(model)).all()
            for row in rows:
                target_session.add(model(**row.model_dump()))
            target_session.commit()
            print(f"{model.__tablename__}: copied {len(rows)} row(s)")
            total_copied += len(rows)

    with target_engine.connect() as conn:
        for model in TABLES_IN_ORDER:
            pk_columns = list(model.__table__.primary_key.columns)
            if len(pk_columns) != 1 or pk_columns[0].type.python_type is not int:
                continue  # non-integer or composite primary keys have no sequence to reset
            table, column = model.__tablename__, pk_columns[0].name
            conn.execute(text(
                f"SELECT setval(pg_get_serial_sequence('{table}', '{column}'), "
                f"COALESCE((SELECT MAX({column}) FROM {table}), 1), "
                f"(SELECT MAX({column}) FROM {table}) IS NOT NULL)"
            ))
        conn.commit()

    print(f"\nDone -- {total_copied} total row(s) migrated.")


if __name__ == "__main__":
    main()
