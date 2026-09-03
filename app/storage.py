"""
Filesystem layout for per-wall-set uploads and generated artifacts.

storage/wall_sets/<id>/manifest.csv       -- the uploaded TikTok CSV export
storage/wall_sets/<id>/labels.pdf         -- the uploaded master shipping-label/
                                              packing-slip PDF (path saved to
                                              WallSet.pdf_file_path)
storage/wall_sets/<id>/label_sheet.pdf    -- the generated barcode sheet for
                                              the wall's manifest
storage/squishy_types/<id>/label_sheet.pdf -- the generated barcode sheet for
                                              a single catalog entry (print
                                              one type's barcode on demand,
                                              outside of any wall set)

Kept out of git (storage/ is gitignored) since every wall set's upload
carries the same real buyer PII as sample_data/.
"""
from pathlib import Path

STORAGE_ROOT = Path("storage")


def wall_set_dir(wall_set_id: int) -> Path:
    path = STORAGE_ROOT / "wall_sets" / str(wall_set_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def manifest_csv_path(wall_set_id: int) -> Path:
    return wall_set_dir(wall_set_id) / "manifest.csv"


def labels_pdf_path(wall_set_id: int) -> Path:
    return wall_set_dir(wall_set_id) / "labels.pdf"


def label_sheet_pdf_path(wall_set_id: int) -> Path:
    return wall_set_dir(wall_set_id) / "label_sheet.pdf"


def squishy_type_dir(squishy_type_id: int) -> Path:
    path = STORAGE_ROOT / "squishy_types" / str(squishy_type_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def squishy_type_label_sheet_pdf_path(squishy_type_id: int) -> Path:
    return squishy_type_dir(squishy_type_id) / "label_sheet.pdf"
