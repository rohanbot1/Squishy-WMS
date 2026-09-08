"""
One-off script: uploads every wall set's manifest.csv and labels.pdf from
the local storage/ folder into R2, through the exact same
save_wall_set_upload() function app/storage.py already uses for a live
upload -- so it writes the same key layout (wall_sets/<id>/manifest.csv,
wall_sets/<id>/labels.pdf), meaning WallSet.pdf_file_path values already
in the database keep working unchanged once R2 is configured. No database
update needed alongside this script.

Deliberately does not touch any lingering label_sheet.pdf files from
before the barcode-sheet generation change (app/barcode_gen.py now
returns bytes directly, never writing one) -- those were always
regenerated fresh on every request and were never meant to persist; this
only migrates the two genuinely persistent upload artifacts.

Requires R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, and
R2_BUCKET_NAME to already be set in the environment (the same variables
app/storage.py checks for at runtime) -- fails loudly if they're missing
rather than silently writing to the local backend instead.

Usage:
    R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET_NAME=... \
        python scripts/migrate_storage_to_r2.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import storage

WALL_SETS_DIR = os.path.join(os.path.dirname(__file__), "..", "storage", "wall_sets")


def main() -> None:
    required = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print(f"Missing required env var(s): {', '.join(missing)}")
        sys.exit(1)

    if not os.path.isdir(WALL_SETS_DIR):
        print(f"No local storage found at {os.path.abspath(WALL_SETS_DIR)} -- nothing to migrate.")
        return

    entries = sorted(os.listdir(WALL_SETS_DIR), key=lambda s: int(s) if s.isdigit() else s)
    migrated = 0
    for wall_set_id_str in entries:
        wall_set_dir = os.path.join(WALL_SETS_DIR, wall_set_id_str)
        if not os.path.isdir(wall_set_dir) or not wall_set_id_str.isdigit():
            continue

        csv_path = os.path.join(wall_set_dir, "manifest.csv")
        pdf_path = os.path.join(wall_set_dir, "labels.pdf")
        if not (os.path.exists(csv_path) and os.path.exists(pdf_path)):
            print(f"Skipping wall set {wall_set_id_str}: missing manifest.csv or labels.pdf "
                  "(never had orders uploaded)")
            continue

        with open(csv_path, "rb") as f:
            csv_bytes = f.read()
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        key = storage.save_wall_set_upload(int(wall_set_id_str), csv_bytes, pdf_bytes)
        print(f"Wall set {wall_set_id_str}: uploaded to {key} "
              f"({len(csv_bytes)} + {len(pdf_bytes)} bytes)")
        migrated += 1

    print(f"\nDone -- {migrated} wall set(s) migrated to R2.")


if __name__ == "__main__":
    main()
