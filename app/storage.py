"""
Storage for wall-set uploads -- the only files that need to persist
across requests. Local filesystem by default (what local dev and Binit's
LAN deployment use, unchanged); switches to Cloudflare R2 (S3-compatible)
automatically when R2_ACCOUNT_ID is set -- same presence-of-env-var
pattern as DATABASE_URL in app/database.py, so nothing about the local
setup needs to change or even know R2 exists.

wall_sets/<id>/manifest.csv -- the uploaded TikTok CSV export, kept as a
                                record (its data already lives in
                                Shipment/ShipmentRequirement rows once
                                parsed -- nothing reads this file back)
wall_sets/<id>/labels.pdf   -- the uploaded master shipping-label/
                                packing-slip PDF. The key returned by
                                save_wall_set_upload() is what gets saved
                                on WallSet.pdf_file_path, and is what
                                read_pdf() takes back later to re-extract
                                a single shipment's label (possibly much
                                after the original upload request).

Generated, immediately-served artifacts (the barcode label sheets) are
deliberately NOT stored here at all -- see app/barcode_gen.py, which
returns PDF bytes directly instead of writing a file, since nothing ever
reads a generated sheet back after the request that asked for it.

Kept out of git (storage/ is gitignored) since every wall set's upload
carries the same real buyer PII as sample_data/.
"""
import os
from collections import OrderedDict
from pathlib import Path

LOCAL_STORAGE_ROOT = Path("storage")

# Measured against a real R2 bucket with a realistic 13MB master PDF:
# re-fetching the whole file over the network for every single shipment
# completion cost 1.5-3s per request -- a real, repeated lag across a
# stream's worth of scans, not a rounding error. This cache keeps the
# handful of wall sets actively being packed at once in memory so only
# the first extraction per wall set pays that cost; local-disk reads
# don't get this treatment since they're already fast enough not to need
# it. Small bounded size, not a wall-set-lifetime cache -- this is about
# one active packing session reusing one fetch, not permanent storage.
_PDF_CACHE_MAX_ENTRIES = 4
_pdf_cache: "OrderedDict[str, bytes]" = OrderedDict()


def _cache_get(key: str) -> bytes | None:
    if key not in _pdf_cache:
        return None
    _pdf_cache.move_to_end(key)  # mark as most recently used
    return _pdf_cache[key]


def _cache_put(key: str, data: bytes) -> None:
    _pdf_cache[key] = data
    _pdf_cache.move_to_end(key)
    while len(_pdf_cache) > _PDF_CACHE_MAX_ENTRIES:
        _pdf_cache.popitem(last=False)  # evict least-recently-used


def _use_r2() -> bool:
    return bool(os.environ.get("R2_ACCOUNT_ID"))


def _r2_client():
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    )


def _write(key: str, data: bytes) -> None:
    if _use_r2():
        _r2_client().put_object(Bucket=os.environ["R2_BUCKET_NAME"], Key=key, Body=data)
        return
    path = LOCAL_STORAGE_ROOT / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _read(key: str) -> bytes:
    if _use_r2():
        obj = _r2_client().get_object(Bucket=os.environ["R2_BUCKET_NAME"], Key=key)
        return obj["Body"].read()
    return (LOCAL_STORAGE_ROOT / key).read_bytes()


def save_wall_set_upload(wall_set_id: int, csv_bytes: bytes, pdf_bytes: bytes) -> str:
    """Persists both uploaded files. Returns the PDF's storage key, to be
    saved on WallSet.pdf_file_path and passed back to read_pdf() later."""
    _write(f"wall_sets/{wall_set_id}/manifest.csv", csv_bytes)
    pdf_key = f"wall_sets/{wall_set_id}/labels.pdf"
    _write(pdf_key, pdf_bytes)
    # Guards against a stale cache entry in the (currently impossible, but
    # cheap to guard) case of a second upload ever reusing the same key.
    _pdf_cache.pop(pdf_key, None)
    return pdf_key


def read_pdf(key: str) -> bytes:
    if not _use_r2():
        return _read(key)  # local disk is already fast enough not to need caching

    cached = _cache_get(key)
    if cached is not None:
        return cached
    data = _read(key)
    _cache_put(key, data)
    return data
