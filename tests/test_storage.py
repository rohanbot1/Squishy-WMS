"""
Unit tests for app/storage.py's backend selection (local filesystem vs
R2), independent of the full HTTP API -- test_api.py already exercises
the local backend indirectly through real upload/download requests; these
tests cover the R2 code path directly (mocked, since this machine has no
real R2 credentials) and the local backend's key-based interface in
isolation.
"""
import sys
import os
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import storage


@pytest.fixture(autouse=True)
def clear_pdf_cache():
    """The R2 read cache is deliberately module-level state (see
    app/storage.py) so it survives across requests within one running
    process -- exactly what makes it useful there makes it a cross-test
    contamination risk here if left alone between tests."""
    storage._pdf_cache.clear()
    yield
    storage._pdf_cache.clear()


def test_local_backend_round_trips_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "LOCAL_STORAGE_ROOT", tmp_path / "storage")
    monkeypatch.delenv("R2_ACCOUNT_ID", raising=False)

    pdf_key = storage.save_wall_set_upload(42, b"csv,data\n1,2", b"%PDF-fake-bytes")
    assert pdf_key == "wall_sets/42/labels.pdf"

    assert storage.read_pdf(pdf_key) == b"%PDF-fake-bytes"
    assert (tmp_path / "storage" / "wall_sets" / "42" / "manifest.csv").read_bytes() == b"csv,data\n1,2"


def test_r2_backend_used_when_configured(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "test-account")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("R2_BUCKET_NAME", "test-bucket")

    mock_client = MagicMock()
    mock_client.get_object.return_value = {"Body": MagicMock(read=lambda: b"%PDF-from-r2")}

    with patch("boto3.client", return_value=mock_client) as mock_boto_client:
        pdf_key = storage.save_wall_set_upload(7, b"csv-bytes", b"pdf-bytes")

        assert pdf_key == "wall_sets/7/labels.pdf"
        mock_boto_client.assert_called_with(
            "s3",
            endpoint_url="https://test-account.r2.cloudflarestorage.com",
            aws_access_key_id="test-key",
            aws_secret_access_key="test-secret",
        )
        assert mock_client.put_object.call_count == 2
        mock_client.put_object.assert_any_call(
            Bucket="test-bucket", Key="wall_sets/7/manifest.csv", Body=b"csv-bytes",
        )
        mock_client.put_object.assert_any_call(
            Bucket="test-bucket", Key="wall_sets/7/labels.pdf", Body=b"pdf-bytes",
        )

        result = storage.read_pdf(pdf_key)
        assert result == b"%PDF-from-r2"
        mock_client.get_object.assert_called_with(Bucket="test-bucket", Key="wall_sets/7/labels.pdf")


def _configure_r2(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "test-account")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("R2_BUCKET_NAME", "test-bucket")


def test_r2_reads_are_cached_after_the_first_fetch(monkeypatch):
    """Measured against a real R2 bucket with a realistic 13MB PDF: every
    uncached read cost 1.5-3s. This is the fix -- a second read_pdf() for
    the same key must not hit the network again at all."""
    _configure_r2(monkeypatch)
    mock_client = MagicMock()
    mock_client.get_object.return_value = {"Body": MagicMock(read=lambda: b"%PDF-master")}

    with patch("boto3.client", return_value=mock_client):
        first = storage.read_pdf("wall_sets/9/labels.pdf")
        second = storage.read_pdf("wall_sets/9/labels.pdf")
        third = storage.read_pdf("wall_sets/9/labels.pdf")

    assert first == second == third == b"%PDF-master"
    assert mock_client.get_object.call_count == 1  # only the first read actually fetched


def test_r2_cache_evicts_least_recently_used_beyond_max_entries(monkeypatch):
    _configure_r2(monkeypatch)
    mock_client = MagicMock()
    mock_client.get_object.side_effect = lambda Bucket, Key: {
        "Body": MagicMock(read=lambda: f"data-for-{Key}".encode())
    }

    with patch("boto3.client", return_value=mock_client):
        # fill the cache past its limit with distinct wall sets
        for i in range(storage._PDF_CACHE_MAX_ENTRIES + 1):
            storage.read_pdf(f"wall_sets/{i}/labels.pdf")

        assert len(storage._pdf_cache) == storage._PDF_CACHE_MAX_ENTRIES
        # the very first one (least recently used) should have been evicted
        assert "wall_sets/0/labels.pdf" not in storage._pdf_cache

        calls_before = mock_client.get_object.call_count
        storage.read_pdf("wall_sets/0/labels.pdf")  # evicted -- must re-fetch
        assert mock_client.get_object.call_count == calls_before + 1


def test_save_wall_set_upload_invalidates_stale_cache_entry(monkeypatch):
    _configure_r2(monkeypatch)
    mock_client = MagicMock()
    mock_client.get_object.return_value = {"Body": MagicMock(read=lambda: b"original-pdf")}

    with patch("boto3.client", return_value=mock_client):
        pdf_key = storage.save_wall_set_upload(3, b"csv", b"original-bytes")
        storage.read_pdf(pdf_key)  # populates the cache
        assert pdf_key in storage._pdf_cache

        storage.save_wall_set_upload(3, b"csv", b"new-bytes")
        assert pdf_key not in storage._pdf_cache  # must not serve stale bytes on the next read


def test_local_backend_reads_are_never_cached(tmp_path, monkeypatch):
    """Local disk is already fast enough not to need this -- and more
    importantly, caching it would be actively wrong for local/LAN use,
    where the file can legitimately change between reads."""
    monkeypatch.setattr(storage, "LOCAL_STORAGE_ROOT", tmp_path / "storage")
    monkeypatch.delenv("R2_ACCOUNT_ID", raising=False)

    pdf_key = storage.save_wall_set_upload(1, b"csv", b"version-1")
    assert storage.read_pdf(pdf_key) == b"version-1"
    assert len(storage._pdf_cache) == 0
