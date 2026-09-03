"""
API-level tests for the Phase 2 FastAPI wrapper. Uses FastAPI's TestClient
against an isolated in-memory database and a temp storage/ root -- never
touches the real squishy_wms.db or storage/ directory.

The upload and scan-to-complete tests reuse the same real TikTok export as
tests/test_with_real_files.py and are skipped if sample_data/ isn't present
locally (it's gitignored real customer data, same as that file assumes).
"""
import csv
import os
import sys
from collections import defaultdict

import fitz  # PyMuPDF
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import storage
from app.api import app
from app.database import get_session
from app.models import Shipment, ShipmentRequirement

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "sample_data", "To_Ship_order-2026-09-01-17_51.csv")
PDF_PATH = os.path.join(os.path.dirname(__file__), "..", "sample_data", "09-01_14-50-39_Shipping_label_Packing_slip.pdf")
HAS_SAMPLE_DATA = os.path.exists(CSV_PATH) and os.path.exists(PDF_PATH)


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(engine, tmp_path, monkeypatch):
    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    monkeypatch.setattr("app.api.init_db", lambda: None)
    monkeypatch.setattr(storage, "STORAGE_ROOT", tmp_path / "storage")

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def _seed_catalog_from_csv(client, csv_path):
    """Mirrors tests/test_with_real_files.py's seeding: one SquishyType per
    distinct Product Name in the export, standing in for the wall already
    having been built with these types."""
    names = set()
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [h.strip() for h in reader.fieldnames]
        for row in reader:
            name = (row.get("Product Name") or "").strip()
            if name:
                names.add(name)

    catalog = {}
    for i, name in enumerate(sorted(names)):
        code = f"SQ{i:04d}"
        resp = client.post("/squishy-types", json={"name": name, "internal_code": code})
        assert resp.status_code == 200, resp.text
        catalog[name] = {"id": resp.json()["id"], "internal_code": code}
    return catalog


def _find_single_item_order(csv_path):
    """Returns (tracking_number, product_name) for an order that's exactly
    one unit of one product, so a single scan is guaranteed to complete it."""
    groups = defaultdict(list)
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [h.strip() for h in reader.fieldnames]
        for row in reader:
            row = {k.strip(): (v.strip() if v else v) for k, v in row.items()}
            tracking = row.get("Tracking ID", "")
            if tracking:
                groups[tracking].append(row)

    for tracking, rows in groups.items():
        if len(rows) == 1 and int(rows[0].get("Quantity", "0") or 0) == 1:
            return tracking, rows[0]["Product Name"]
    return None, None


def _upload_sample_data(client, wall_set_id):
    with open(CSV_PATH, "rb") as csv_f, open(PDF_PATH, "rb") as pdf_f:
        return client.post(
            f"/wall-sets/{wall_set_id}/upload",
            files={
                "csv_file": ("orders.csv", csv_f, "text/csv"),
                "pdf_file": ("labels.pdf", pdf_f, "application/pdf"),
            },
        )


# --- auth --------------------------------------------------------------------

TEST_ADMIN_PASSWORD = "correct horse battery staple"


@pytest.fixture
def admin_password(monkeypatch):
    from app.auth import hash_password
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password(TEST_ADMIN_PASSWORD))
    return TEST_ADMIN_PASSWORD


@pytest.fixture
def admin_client(client, admin_password):
    """A TestClient already logged in as the admin -- for tests that just
    need an authenticated session and aren't testing login itself."""
    resp = client.post("/auth/login", json={"password": admin_password})
    assert resp.status_code == 200
    return client


def test_login_with_correct_password_sets_session_cookie(client, admin_password):
    resp = client.post("/auth/login", json={"password": admin_password})
    assert resp.status_code == 200
    assert resp.json() == {"authenticated": True}
    assert "session" in resp.cookies


def test_login_with_wrong_password_rejected(client, admin_password):
    resp = client.post("/auth/login", json={"password": "wrong"})
    assert resp.status_code == 401


def test_login_without_admin_password_hash_configured_500s(client, monkeypatch):
    # explicit delenv, not just "don't use the admin_password fixture" --
    # app.api's load_dotenv() at import time may have already pulled a real
    # ADMIN_PASSWORD_HASH from a local .env into the process environment
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    resp = client.post("/auth/login", json={"password": "anything"})
    assert resp.status_code == 500


def test_me_without_session_401s(client, admin_password):
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_me_with_valid_session_200s(client, admin_password):
    client.post("/auth/login", json={"password": admin_password})
    resp = client.get("/auth/me")
    assert resp.status_code == 200
    assert resp.json() == {"authenticated": True}


def test_logout_invalidates_session(client, admin_password):
    client.post("/auth/login", json={"password": admin_password})
    resp = client.post("/auth/logout")
    assert resp.status_code == 200

    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_expired_session_rejected(client, admin_password, engine):
    from datetime import datetime, timedelta
    from app.models import AdminSession

    with Session(engine) as session:
        session.add(AdminSession(
            token="expiredtoken123",
            expires_at=datetime.utcnow() - timedelta(days=1),
        ))
        session.commit()

    client.cookies.set("session", "expiredtoken123")
    resp = client.get("/auth/me")
    assert resp.status_code == 401


# --- squishy type catalog ---------------------------------------------------

def test_create_and_list_squishy_types(client):
    resp = client.post("/squishy-types", json={"name": "Yellow Butter", "internal_code": "SQ0001"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Yellow Butter"
    assert body["internal_code"] == "SQ0001"
    assert body["is_giveaway_item"] is False

    resp = client.get("/squishy-types")
    assert resp.status_code == 200
    names = [t["name"] for t in resp.json()]
    assert "Yellow Butter" in names


def test_create_squishy_type_duplicate_name_rejected(client):
    client.post("/squishy-types", json={"name": "Dup", "internal_code": "SQ0002"})
    resp = client.post("/squishy-types", json={"name": "Dup", "internal_code": "SQ0003"})
    assert resp.status_code == 409


def test_create_squishy_type_without_internal_code_auto_generates_one(client):
    resp = client.post("/squishy-types", json={"name": "Auto Coded"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["internal_code"]  # non-empty, server-assigned

    # a second auto-coded type gets a different code than the first
    resp2 = client.post("/squishy-types", json={"name": "Auto Coded Two"})
    assert resp2.status_code == 200
    assert resp2.json()["internal_code"] != body["internal_code"]


# --- wall sets ---------------------------------------------------------------

def test_create_wall_set_with_items_and_fetch_it(client):
    t1 = client.post("/squishy-types", json={"name": "A", "internal_code": "SQA"}).json()
    t2 = client.post("/squishy-types", json={"name": "B", "internal_code": "SQB"}).json()

    resp = client.post("/wall-sets", json={
        "label": "test wall",
        "items": [
            {"squishy_type_id": t1["id"], "quantity": 3},
            {"squishy_type_id": t2["id"], "quantity": 5},
        ],
    })
    assert resp.status_code == 200
    wall_set_id = resp.json()["id"]
    assert len(resp.json()["items"]) == 2

    resp = client.get(f"/wall-sets/{wall_set_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "test wall"
    assert {i["squishy_type_id"] for i in body["items"]} == {t1["id"], t2["id"]}

    resp = client.get("/wall-sets")
    assert wall_set_id in [w["id"] for w in resp.json()]


def test_get_unknown_wall_set_404s(client):
    resp = client.get("/wall-sets/999")
    assert resp.status_code == 404


def test_list_shipments_returns_open_and_complete_with_nested_requirements(client, engine):
    t1 = client.post("/squishy-types", json={"name": "Yellow Butter", "internal_code": "SQY"}).json()
    t2 = client.post("/squishy-types", json={"name": "Sugar Baby", "internal_code": "SQS"}).json()
    wall_set_id = client.post("/wall-sets", json={"label": "shipments test"}).json()["id"]

    with Session(engine) as session:
        open_bundle = Shipment(wall_set_id=wall_set_id, tracking_number="OPEN001", order_ids="O1", bin_number=1)
        session.add(open_bundle)
        session.flush()
        session.add(ShipmentRequirement(
            shipment_id=open_bundle.id, squishy_type_id=t1["id"], quantity_required=2, quantity_scanned=1,
        ))
        session.add(ShipmentRequirement(
            shipment_id=open_bundle.id, squishy_type_id=t2["id"], quantity_required=1, quantity_scanned=0,
        ))

        completed = Shipment(
            wall_set_id=wall_set_id, tracking_number="DONE001", order_ids="O2",
            bin_number=2, is_complete=True,
        )
        session.add(completed)
        session.flush()
        session.add(ShipmentRequirement(
            shipment_id=completed.id, squishy_type_id=t1["id"], quantity_required=1, quantity_scanned=1,
        ))
        session.commit()
        open_id, completed_id = open_bundle.id, completed.id

    resp = client.get(f"/wall-sets/{wall_set_id}/shipments")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    by_id = {s["id"]: s for s in body}

    open_shipment = by_id[open_id]
    assert open_shipment["tracking_number"] == "OPEN001"
    assert open_shipment["is_complete"] is False
    assert open_shipment["bin_number"] == 1
    req_by_type = {r["squishy_type_id"]: r for r in open_shipment["requirements"]}
    assert req_by_type[t1["id"]] == {
        "squishy_type_id": t1["id"], "name": "Yellow Butter",
        "quantity_required": 2, "quantity_scanned": 1,
    }
    assert req_by_type[t2["id"]]["quantity_scanned"] == 0

    completed_shipment = by_id[completed_id]
    assert completed_shipment["is_complete"] is True
    assert completed_shipment["tracking_number"] == "DONE001"
    assert len(completed_shipment["requirements"]) == 1


def test_list_shipments_for_wall_set_with_none_returns_empty_list(client):
    wall_set = client.post("/wall-sets", json={"label": "empty shipments test"}).json()
    resp = client.get(f"/wall-sets/{wall_set['id']}/shipments")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_shipments_unknown_wall_set_404s(client):
    resp = client.get("/wall-sets/999/shipments")
    assert resp.status_code == 404


def test_label_sheet_download(client):
    t1 = client.post("/squishy-types", json={"name": "A", "internal_code": "SQA"}).json()
    wall_set = client.post("/wall-sets", json={
        "label": "sheet test",
        "items": [{"squishy_type_id": t1["id"], "quantity": 2}],
    }).json()

    resp = client.get(f"/wall-sets/{wall_set['id']}/label-sheet")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert len(resp.content) > 0


def test_squishy_type_label_sheet_download(client):
    t1 = client.post("/squishy-types", json={"name": "Solo Type", "internal_code": "SQSOLO"}).json()

    resp = client.get(f"/squishy-types/{t1['id']}/label-sheet")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    doc = fitz.open(stream=resp.content, filetype="pdf")
    assert len(doc) == 1  # default quantity
    doc.close()

    resp = client.get(f"/squishy-types/{t1['id']}/label-sheet", params={"quantity": 3})
    assert resp.status_code == 200
    doc = fitz.open(stream=resp.content, filetype="pdf")
    assert len(doc) == 3
    for page in doc:
        assert (page.rect.width, page.rect.height) == (144.0, 72.0)  # 2in x 1in
    doc.close()


def test_squishy_type_label_sheet_unknown_type_404s(client):
    resp = client.get("/squishy-types/999/label-sheet")
    assert resp.status_code == 404


def test_squishy_type_label_sheet_rejects_zero_quantity(client):
    t1 = client.post("/squishy-types", json={"name": "Zero Qty", "internal_code": "SQZERO"}).json()
    resp = client.get(f"/squishy-types/{t1['id']}/label-sheet", params={"quantity": 0})
    assert resp.status_code == 400


# --- upload / ingestion ------------------------------------------------------

@pytest.mark.skipif(not HAS_SAMPLE_DATA, reason="requires a real export in sample_data/")
def test_upload_produces_ingestion_summary(client):
    _seed_catalog_from_csv(client, CSV_PATH)
    wall_set = client.post("/wall-sets", json={"label": "upload test"}).json()

    resp = _upload_sample_data(client, wall_set["id"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["shipments_created"] > 0
    assert body["requirements_created"] > 0
    assert body["unmatched_products"] == []
    assert body["labels_matched"] == body["shipments_created"]


@pytest.mark.skipif(not HAS_SAMPLE_DATA, reason="requires a real export in sample_data/")
def test_upload_highlights_unmatched_products_when_catalog_incomplete(client):
    # deliberately skip seeding the catalog -- every product name should
    # come back unmatched
    wall_set = client.post("/wall-sets", json={"label": "incomplete catalog test"}).json()
    resp = _upload_sample_data(client, wall_set["id"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["requirements_created"] == 0
    assert len(body["unmatched_products"]) > 0


# --- scanning ------------------------------------------------------------

@pytest.mark.skipif(not HAS_SAMPLE_DATA, reason="requires a real export in sample_data/")
def test_scan_to_complete_and_download_label(client):
    catalog = _seed_catalog_from_csv(client, CSV_PATH)
    wall_set = client.post("/wall-sets", json={"label": "scan test"}).json()
    wall_set_id = wall_set["id"]

    _upload_sample_data(client, wall_set_id)

    resp = client.post(f"/wall-sets/{wall_set_id}/scan", json={"barcode": "NOT-A-REAL-CODE"})
    assert resp.json() == {"status": "unknown_barcode"}

    tracking, product_name = _find_single_item_order(CSV_PATH)
    assert tracking is not None, "sample export has no single-item order to test against"
    barcode = catalog[product_name]["internal_code"]

    resp = client.post(f"/wall-sets/{wall_set_id}/scan", json={"barcode": barcode})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "complete"
    assert body["tracking_number"] == tracking
    shipment_id = body["shipment_id"]

    # exhaust every other open shipment's need for this same product, then
    # confirm the scan-with-nothing-open path is reached deterministically
    total_needed = 0
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [h.strip() for h in reader.fieldnames]
        for row in reader:
            if (row.get("Product Name") or "").strip() == product_name:
                total_needed += int(row.get("Quantity", "0") or 0)

    for _ in range(total_needed - 1):  # the completed shipment above used one
        resp = client.post(f"/wall-sets/{wall_set_id}/scan", json={"barcode": barcode})
        assert resp.json()["status"] in {"in_progress", "complete"}

    resp = client.post(f"/wall-sets/{wall_set_id}/scan", json={"barcode": barcode})
    assert resp.json()["status"] == "no_shipment_needs_it"

    resp = client.get(f"/wall-sets/{wall_set_id}/shipments/{shipment_id}/label")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert len(resp.content) > 0

    # label page + packing slip, not just the label
    doc = fitz.open(stream=resp.content, filetype="pdf")
    assert len(doc) == 2
    packing_slip_text = doc[1].get_text()
    assert "Tracking number:" in packing_slip_text
    assert tracking in "".join(ch for ch in packing_slip_text if ch.isdigit())
    doc.close()


def test_scan_against_wall_set_with_no_shipments_returns_no_match(client):
    t1 = client.post("/squishy-types", json={"name": "Lonely Squishy", "internal_code": "SQLONE"}).json()
    wall_set = client.post("/wall-sets", json={
        "label": "empty scan test",
        "items": [{"squishy_type_id": t1["id"], "quantity": 1}],
    }).json()

    resp = client.post(f"/wall-sets/{wall_set['id']}/scan", json={"barcode": "SQLONE"})
    assert resp.json()["status"] == "no_shipment_needs_it"


# --- financials (admin-only) -------------------------------------------------

def _make_wall_set_with_items(client, label="financials test"):
    # names/codes vary by label so this helper can be called more than once
    # in the same test (e.g. two wall sets) without a 409 name collision
    t1 = client.post("/squishy-types", json={
        "name": f"Yellow Butter ({label})", "internal_code": f"SQFY-{label}",
    }).json()
    t2 = client.post("/squishy-types", json={
        "name": f"Sugar Baby ({label})", "internal_code": f"SQFS-{label}", "is_giveaway_item": True,
    }).json()
    wall_set = client.post("/wall-sets", json={
        "label": label,
        "items": [
            {"squishy_type_id": t1["id"], "quantity": 10},
            {"squishy_type_id": t2["id"], "quantity": 5},
        ],
    }).json()
    return wall_set, t1, t2


def test_financials_routes_require_admin(client):
    wall_set, _, _ = _make_wall_set_with_items(client)
    body = {
        "streamer": "Binit", "stream_started_at": "2026-09-01T18:00:00",
        "stream_ended_at": "2026-09-01T20:00:00", "revenue": 100, "fees": 10,
        "bid_average": 1.5, "item_costs": [],
    }
    assert client.put(f"/wall-sets/{wall_set['id']}/financials", json=body).status_code == 401
    assert client.get(f"/wall-sets/{wall_set['id']}/financials").status_code == 401
    assert client.get("/financials").status_code == 401


def test_upsert_financials_creates_then_updates(admin_client, engine):
    wall_set, t1, t2 = _make_wall_set_with_items(admin_client)

    create_body = {
        "streamer": "Binit",
        "stream_started_at": "2026-09-01T18:00:00",
        "stream_ended_at": "2026-09-01T20:30:00",
        "revenue": 500.0, "fees": 25.0, "bid_average": 2.5,
        "giveaway_squishy_type_id": t2["id"], "giveaway_quantity": 1,
        "notes": "first pass",
        "item_costs": [
            {"squishy_type_id": t1["id"], "unit_cost": 3.0},   # 10 * 3.0 = 30
            {"squishy_type_id": t2["id"], "unit_cost": 2.0},   # 5 * 2.0 = 10
        ],
    }
    resp = admin_client.put(f"/wall-sets/{wall_set['id']}/financials", json=create_body)
    assert resp.status_code == 200
    body = resp.json()
    assert body["wall_set_id"] == wall_set["id"]
    assert body["total_item_cost"] == 40.0
    assert body["profit"] == 500.0 - 25.0 - 40.0  # 435.0
    assert body["roi"] == pytest.approx((500.0 - 25.0 - 40.0) / 40.0)
    assert body["duration_seconds"] == 2.5 * 3600
    assert body["giveaway"] == {"squishy_type_id": t2["id"], "name": t2["name"], "quantity": 1}
    assert {i["squishy_type_id"]: i["quantity"] for i in body["items"]} == {t1["id"]: 10, t2["id"]: 5}

    # only one record exists per wall set -- confirm via a direct DB check
    from app.models import FinancialRecord
    with Session(engine) as session:
        records = session.exec(
            select(FinancialRecord).where(FinancialRecord.wall_set_id == wall_set["id"])
        ).all()
        assert len(records) == 1

    # update: different revenue and only one item costed now
    update_body = {**create_body, "revenue": 600.0,
                   "item_costs": [{"squishy_type_id": t1["id"], "unit_cost": 3.0}]}
    resp = admin_client.put(f"/wall-sets/{wall_set['id']}/financials", json=update_body)
    assert resp.status_code == 200
    body = resp.json()
    assert body["revenue"] == 600.0
    assert body["total_item_cost"] == 30.0  # t2's cost entry was replaced away, not appended to

    with Session(engine) as session:
        records = session.exec(
            select(FinancialRecord).where(FinancialRecord.wall_set_id == wall_set["id"])
        ).all()
        assert len(records) == 1  # still exactly one -- upsert, not a duplicate


def test_financials_zero_item_cost_roi_is_null(admin_client):
    """The case this was specifically flagged for: Binit records revenue/fees
    right after a stream and fills in item costs later. total_item_cost is a
    real, expected 0 in that window -- roi must come back null, not a
    divide-by-zero error or an invalid value."""
    wall_set, t1, t2 = _make_wall_set_with_items(admin_client)

    body = {
        "streamer": "Binit",
        "stream_started_at": "2026-09-01T18:00:00",
        "stream_ended_at": "2026-09-01T20:00:00",
        "revenue": 500.0, "fees": 25.0, "bid_average": 2.5,
        "item_costs": [],  # nothing costed yet
    }
    resp = admin_client.put(f"/wall-sets/{wall_set['id']}/financials", json=body)
    assert resp.status_code == 200
    created = resp.json()
    assert created["total_item_cost"] == 0.0
    assert created["roi"] is None
    assert created["profit"] == 475.0  # revenue - fees, no item cost yet

    # read it back fresh too, not just the write response
    resp = admin_client.get(f"/wall-sets/{wall_set['id']}/financials")
    assert resp.status_code == 200
    fetched = resp.json()
    assert fetched["total_item_cost"] == 0.0
    assert fetched["roi"] is None


def test_get_financials_for_wall_set_without_record_404s(admin_client):
    wall_set, _, _ = _make_wall_set_with_items(admin_client)
    resp = admin_client.get(f"/wall-sets/{wall_set['id']}/financials")
    assert resp.status_code == 404


def test_upsert_financials_unknown_wall_set_404s(admin_client):
    resp = admin_client.put("/wall-sets/999/financials", json={
        "streamer": "Binit", "stream_started_at": "2026-09-01T18:00:00",
        "stream_ended_at": "2026-09-01T20:00:00", "revenue": 1, "fees": 0,
        "bid_average": 0, "item_costs": [],
    })
    assert resp.status_code == 404


def test_upsert_financials_invalid_giveaway_type_400s(admin_client):
    wall_set, _, _ = _make_wall_set_with_items(admin_client)
    resp = admin_client.put(f"/wall-sets/{wall_set['id']}/financials", json={
        "streamer": "Binit", "stream_started_at": "2026-09-01T18:00:00",
        "stream_ended_at": "2026-09-01T20:00:00", "revenue": 1, "fees": 0,
        "bid_average": 0, "giveaway_squishy_type_id": 999, "item_costs": [],
    })
    assert resp.status_code == 400


def test_list_financials_summarizes_all_records(admin_client):
    wall_set_a, t1a, _ = _make_wall_set_with_items(admin_client, label="stream A")
    wall_set_b, t1b, _ = _make_wall_set_with_items(admin_client, label="stream B")

    admin_client.put(f"/wall-sets/{wall_set_a['id']}/financials", json={
        "streamer": "Binit", "stream_started_at": "2026-09-01T18:00:00",
        "stream_ended_at": "2026-09-01T20:00:00", "revenue": 100.0, "fees": 10.0,
        "bid_average": 1.0, "item_costs": [{"squishy_type_id": t1a["id"], "unit_cost": 1.0}],
    })
    admin_client.put(f"/wall-sets/{wall_set_b['id']}/financials", json={
        "streamer": "Binit", "stream_started_at": "2026-09-02T18:00:00",
        "stream_ended_at": "2026-09-02T20:00:00", "revenue": 200.0, "fees": 20.0,
        "bid_average": 1.0, "item_costs": [],
    })

    resp = admin_client.get("/financials")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    by_wall_set = {r["wall_set_id"]: r for r in body}
    assert by_wall_set[wall_set_a["id"]]["wall_set_label"] == "stream A"
    assert by_wall_set[wall_set_a["id"]]["roi"] is not None
    assert by_wall_set[wall_set_b["id"]]["wall_set_label"] == "stream B"
    assert by_wall_set[wall_set_b["id"]]["roi"] is None  # no item costs entered
