"""
One-off script: seeds the live SquishyType catalog (squishy_wms.db) from a
TikTok CSV export's distinct Product Name column, standing in for "the wall
was already built with these types". Same seeding logic as
tests/test_with_real_files.py's seed_catalog_from_csv, but against the real
database instead of an in-memory test one -- for populating the catalog by
hand before clicking through the app, instead of typing 27 emoji names into
the Wall Builder UI one at a time.

Usage (run from the project root, same venv as the API server):
    python scripts/seed_catalog.py [path/to/export.csv]

Defaults to sample_data/To_Ship_order-2026-09-01-17_51.csv. Safe to re-run:
skips any product name that already matches an existing SquishyType (exact
or whitespace/case-normalized, same fallback the matching engine itself
uses), and never reuses an internal_code already in the catalog.
"""
import csv
import itertools
import os
import sys
from typing import Iterator

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Product names carry emoji; Windows terminals often default stdout to a
# codepage that can't encode them, which would otherwise crash the summary
# print after the catalog rows are already committed.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from sqlmodel import Session, select

from app.database import engine, init_db
from app.models import SquishyType

DEFAULT_CSV_PATH = os.path.join(
    os.path.dirname(__file__), "..", "sample_data", "To_Ship_order-2026-09-01-17_51.csv"
)


def read_product_names(csv_path: str) -> set[str]:
    names = set()
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [h.strip() for h in reader.fieldnames]
        for row in reader:
            name = (row.get("Product Name") or "").strip()
            if name:
                names.add(name)
    return names


def unused_internal_codes(existing_codes: set[str]) -> Iterator[str]:
    for i in itertools.count():
        code = f"SQ{i:04d}"
        if code not in existing_codes:
            yield code


def main() -> None:
    csv_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CSV_PATH
    if not os.path.exists(csv_path):
        print(f"No such file: {csv_path}")
        sys.exit(1)

    init_db()

    with Session(engine) as session:
        existing_types = session.exec(select(SquishyType)).all()
        already_matched = {SquishyType.normalize(t.name) for t in existing_types}
        existing_codes = {t.internal_code for t in existing_types}

        product_names = read_product_names(csv_path)
        to_create = sorted(
            name for name in product_names if SquishyType.normalize(name) not in already_matched
        )

        if not to_create:
            print(f"Catalog already has a match for all {len(product_names)} product name(s) in {csv_path}. Nothing to do.")
            return

        code_gen = unused_internal_codes(existing_codes)
        created = []
        for name in to_create:
            code = next(code_gen)
            session.add(SquishyType(
                name=name,
                internal_code=code,
                is_giveaway_item=(name.strip().upper() == "BUTTER SQUISHY"),
            ))
            created.append((code, name))

        session.commit()

        print(f"Created {len(created)} squishy type(s) from {csv_path}:")
        for code, name in created:
            print(f"  {code}  {name}")

        skipped = len(product_names) - len(to_create)
        if skipped:
            print(f"Skipped {skipped} product name(s) already matched in the catalog.")


if __name__ == "__main__":
    main()
