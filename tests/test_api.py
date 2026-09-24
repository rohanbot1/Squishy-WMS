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
def unauthenticated_client(engine, tmp_path, monkeypatch):
    """The bare app, no login of any kind performed -- for tests that
    verify the floor gate or admin gate themselves. Almost everything else
    wants `client` instead, which layers a floor-PIN login on top of this
    automatically, since floor access is the default assumed state for
    Wall Builder / Packer Scan / Shipments routes in every other test."""
    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    monkeypatch.setattr("app.api.init_db", lambda: None)
    monkeypatch.setattr(storage, "LOCAL_STORAGE_ROOT", tmp_path / "storage")

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


TEST_FLOOR_PIN = "4321"


@pytest.fixture
def client(unauthenticated_client, monkeypatch):
    from app.auth import hash_password
    monkeypatch.setenv("FLOOR_PIN_HASH", hash_password(TEST_FLOOR_PIN))
    resp = unauthenticated_client.post("/api/auth/floor-login", json={"pin": TEST_FLOOR_PIN})
    assert resp.status_code == 200, resp.text
    return unauthenticated_client


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
        resp = client.post("/api/squishy-types", json={"name": name, "internal_code": code})
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
            f"/api/wall-sets/{wall_set_id}/upload",
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
    resp = client.post("/api/auth/login", json={"password": admin_password})
    assert resp.status_code == 200
    return client


def test_login_with_correct_password_sets_session_cookie(client, admin_password):
    resp = client.post("/api/auth/login", json={"password": admin_password})
    assert resp.status_code == 200
    assert resp.json() == {"authenticated": True}
    assert "session" in resp.cookies


def test_login_with_wrong_password_rejected(client, admin_password):
    resp = client.post("/api/auth/login", json={"password": "wrong"})
    assert resp.status_code == 401


def test_login_without_admin_password_hash_configured_500s(client, monkeypatch):
    # explicit delenv, not just "don't use the admin_password fixture" --
    # app.api's load_dotenv() at import time may have already pulled a real
    # ADMIN_PASSWORD_HASH from a local .env into the process environment
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    resp = client.post("/api/auth/login", json={"password": "anything"})
    assert resp.status_code == 500


def test_me_without_session_401s(client, admin_password):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_with_valid_session_200s(client, admin_password):
    client.post("/api/auth/login", json={"password": admin_password})
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json() == {"authenticated": True}


def test_logout_invalidates_session(client, admin_password):
    client.post("/api/auth/login", json={"password": admin_password})
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200

    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_expired_session_rejected(client, admin_password, engine):
    from datetime import datetime, timedelta, timezone
    from app.models import AdminSession

    with Session(engine) as session:
        session.add(AdminSession(
            token="expiredtoken123",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        ))
        session.commit()

    client.cookies.set("session", "expiredtoken123")
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


# --- floor PIN auth ----------------------------------------------------------

@pytest.fixture
def floor_pin_hash_set(unauthenticated_client, monkeypatch):
    """Just the env var, no login performed -- for tests that drive the
    floor-login flow (or its failure modes) themselves."""
    from app.auth import hash_password
    monkeypatch.setenv("FLOOR_PIN_HASH", hash_password(TEST_FLOOR_PIN))
    return unauthenticated_client


def test_floor_login_with_correct_pin_sets_session_cookie(floor_pin_hash_set):
    resp = floor_pin_hash_set.post("/api/auth/floor-login", json={"pin": TEST_FLOOR_PIN})
    assert resp.status_code == 200
    assert resp.json() == {"authenticated": True}
    assert "floor_session" in resp.cookies


def test_floor_login_with_wrong_pin_rejected(floor_pin_hash_set):
    resp = floor_pin_hash_set.post("/api/auth/floor-login", json={"pin": "0000"})
    assert resp.status_code == 401


def test_floor_login_without_floor_pin_hash_configured_500s(unauthenticated_client, monkeypatch):
    monkeypatch.delenv("FLOOR_PIN_HASH", raising=False)
    resp = unauthenticated_client.post("/api/auth/floor-login", json={"pin": "1234"})
    assert resp.status_code == 500


def test_floor_me_without_session_401s(unauthenticated_client):
    resp = unauthenticated_client.get("/api/auth/floor-me")
    assert resp.status_code == 401


def test_floor_me_with_valid_session_200s(floor_pin_hash_set):
    floor_pin_hash_set.post("/api/auth/floor-login", json={"pin": TEST_FLOOR_PIN})
    resp = floor_pin_hash_set.get("/api/auth/floor-me")
    assert resp.status_code == 200
    assert resp.json() == {"authenticated": True}


# Every Wall Builder / Packer Scan / Shipments route -- if even one of
# these were left off (or a future route forgot the floor_router), this
# is what would catch it: a genuine HTTP request, no floor_session
# cookie, must come back 401, not whatever that route's normal response
# would otherwise be.
FLOOR_GATED_ROUTES = [
    ("post", "/api/squishy-types"),
    ("get", "/api/squishy-types"),
    ("get", "/api/squishy-types/999/label-sheet"),
    ("post", "/api/wall-sets"),
    ("get", "/api/wall-sets"),
    ("get", "/api/wall-sets/999"),
    ("get", "/api/wall-sets/999/shipments"),
    ("get", "/api/wall-sets/999/label-sheet"),
    ("post", "/api/wall-sets/999/upload"),
    ("post", "/api/wall-sets/upload"),
    ("post", "/api/wall-sets/999/scan"),
    ("get", "/api/wall-sets/999/shipments/999/label"),
]


@pytest.mark.parametrize("method,path", FLOOR_GATED_ROUTES)
def test_floor_gated_routes_reject_requests_without_floor_session(unauthenticated_client, method, path):
    resp = getattr(unauthenticated_client, method)(path)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Floor PIN not entered"


def test_admin_and_financials_routes_are_not_floor_gated(unauthenticated_client):
    """The two tiers don't stack -- these routes must reject on their own
    (admin) terms, not the floor gate's, even with zero cookies at all."""
    resp = unauthenticated_client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Not logged in"

    resp = unauthenticated_client.get("/api/financials")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Not logged in"

    resp = unauthenticated_client.get("/api/wall-sets/999/financials")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Not logged in"

    # and the auth mechanism routes themselves must not require floor
    # access either -- otherwise nobody could ever reach floor-login
    resp = unauthenticated_client.post("/api/auth/login", json={"password": "whatever"})
    assert resp.status_code in (401, 500)  # rejected on its own terms, not floor-gated


def test_floor_pin_backoff_rejects_even_correct_pin_within_delay_window(floor_pin_hash_set):
    """The backoff must actually block attempts, not just log them --
    submitting the CORRECT pin immediately after a couple of wrong ones
    must still be rejected, proving the delay is enforced before the real
    PIN check ever runs."""
    floor_pin_hash_set.post("/api/auth/floor-login", json={"pin": "0000"})
    floor_pin_hash_set.post("/api/auth/floor-login", json={"pin": "0000"})

    resp = floor_pin_hash_set.post("/api/auth/floor-login", json={"pin": TEST_FLOOR_PIN})
    assert resp.status_code == 401


def test_floor_pin_backoff_does_not_count_attempts_made_within_the_delay(floor_pin_hash_set, engine):
    """Stronger than just "rejected": a guess submitted inside the backoff
    window doesn't even advance the failure count, since verify_floor_pin
    returns early on the timing check before ever reaching the real
    password comparison. An attacker can't out-pace the delay by firing
    guesses faster than it allows -- doing so just wastes guesses."""
    from app.models import PinAttempt

    floor_pin_hash_set.post("/api/auth/floor-login", json={"pin": "0000"})
    floor_pin_hash_set.post("/api/auth/floor-login", json={"pin": "1111"})
    floor_pin_hash_set.post("/api/auth/floor-login", json={"pin": "2222"})

    with Session(engine) as session:
        attempt = session.get(PinAttempt, "testclient")
        assert attempt.failure_count == 1


def test_floor_pin_backoff_allows_correct_pin_once_delay_has_passed(floor_pin_hash_set, engine):
    """Not a hard lockout -- once enough time has passed, a correct PIN
    still works, no matter how many prior failures."""
    from datetime import datetime, timedelta, timezone
    from app.models import PinAttempt

    floor_pin_hash_set.post("/api/auth/floor-login", json={"pin": "0000"})

    with Session(engine) as session:
        attempt = session.get(PinAttempt, "testclient")
        assert attempt is not None
        assert attempt.failure_count == 1
        attempt.last_attempt_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        session.add(attempt)
        session.commit()

    resp = floor_pin_hash_set.post("/api/auth/floor-login", json={"pin": TEST_FLOOR_PIN})
    assert resp.status_code == 200
    assert "floor_session" in resp.cookies


def test_floor_pin_failure_count_resets_after_success(floor_pin_hash_set, engine):
    from datetime import datetime, timedelta, timezone
    from app.models import PinAttempt

    floor_pin_hash_set.post("/api/auth/floor-login", json={"pin": "0000"})

    with Session(engine) as session:
        attempt = session.get(PinAttempt, "testclient")
        attempt.last_attempt_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        session.add(attempt)
        session.commit()

    resp = floor_pin_hash_set.post("/api/auth/floor-login", json={"pin": TEST_FLOOR_PIN})
    assert resp.status_code == 200

    with Session(engine) as session:
        assert session.get(PinAttempt, "testclient") is None


# --- timezone-aware timestamps (Postgres login crash regression) ------------
# SQLModel 0.0.47 rejects naive datetimes on write ("Datetime values must
# have timezone information") and returns aware UTC on read. These pin the
# behaviors that broke on Render: every login/scan write, comparisons
# against rows written before the fix (stored naive), and user-entered
# stream times that must stay exactly as typed.

def test_as_utc_normalizes_naive_and_offset_datetimes():
    from datetime import datetime, timedelta, timezone
    from app.timeutil import as_utc, utc_now

    assert utc_now().utcoffset() == timedelta(0)
    naive = datetime(2026, 9, 1, 12, 0)
    assert as_utc(naive) == datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    eastern = datetime(2026, 9, 1, 8, 0, tzinfo=timezone(timedelta(hours=-4)))
    assert as_utc(eastern) == datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def test_floor_login_writes_timezone_aware_session(floor_pin_hash_set, engine):
    from datetime import timedelta
    from app.models import FloorSession

    resp = floor_pin_hash_set.post("/api/auth/floor-login", json={"pin": TEST_FLOOR_PIN})
    assert resp.status_code == 200, resp.text
    with Session(engine) as session:
        floor_session = session.exec(select(FloorSession)).one()
        assert floor_session.created_at.utcoffset() == timedelta(0)
        assert floor_session.expires_at > floor_session.created_at
    assert floor_pin_hash_set.get("/api/wall-sets").status_code == 200


def test_legacy_naive_rows_compare_without_crashing(floor_pin_hash_set, engine):
    """Rows written before the fix hold naive UTC in the database. Expiry
    checks and PIN backoff math must treat them as UTC -- not raise
    TypeError comparing naive against aware."""
    from sqlalchemy import text

    with Session(engine) as session:
        session.exec(text(
            "INSERT INTO floorsession (token, created_at, expires_at) "
            "VALUES ('legacy-expired', '2020-01-01 00:00:00.000000', '2020-01-02 00:00:00.000000')"))
        session.exec(text(
            "INSERT INTO pinattempt (ip_address, failure_count, last_attempt_at) "
            "VALUES ('testclient', 1, '2020-01-01 00:00:00.000000')"))
        session.commit()

    floor_pin_hash_set.cookies.set("floor_session", "legacy-expired")
    assert floor_pin_hash_set.get("/api/wall-sets").status_code == 401  # expired, not a 500

    # The old failed attempt is long past its backoff window.
    resp = floor_pin_hash_set.post("/api/auth/floor-login", json={"pin": TEST_FLOOR_PIN})
    assert resp.status_code == 200, resp.text


def test_admin_login_and_session_check_work_with_aware_timestamps(client, admin_password, engine):
    from datetime import timedelta
    from app.models import AdminSession

    assert client.post("/api/auth/login", json={"password": admin_password}).status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    with Session(engine) as session:
        assert session.exec(select(AdminSession)).one().expires_at.utcoffset() == timedelta(0)


def test_financial_stream_times_round_trip_exactly_as_entered(admin_client):
    """Typed into a datetime-local input: no zone, no conversion. The
    frontend displays these back as entered, so they must not come back
    shifted or with a UTC offset attached."""
    wall_set, t1, _ = _make_wall_set_with_items(admin_client)
    body = {"streamer": "Binit", "stream_started_at": "2026-09-01T18:00", "stream_ended_at": "2026-09-01T20:30",
            "revenue": 500.0, "fees": 25.0, "bid_average": 2.5, "giveaway_squishy_type_id": None,
            "giveaway_quantity": None, "notes": None, "item_costs": []}
    resp = admin_client.put(f"/api/wall-sets/{wall_set['id']}/financials", json=body)
    assert resp.status_code == 200, resp.text
    got = admin_client.get(f"/api/wall-sets/{wall_set['id']}/financials").json()
    assert got["stream_started_at"] == "2026-09-01T18:00:00"
    assert got["stream_ended_at"] == "2026-09-01T20:30:00"
    assert admin_client.get("/api/financials").json()[0]["stream_started_at"] == "2026-09-01T18:00:00"


def test_scan_to_completion_records_aware_timestamps(client, engine):
    """Scan writes (ScanEvent.scanned_at, Shipment.completed_at) were the
    other production crash site."""
    from datetime import timedelta
    from app.models import ScanEvent

    wall_set = client.post("/api/wall-sets", json={"label": "tz test", "items": []}).json()
    squishy = client.post("/api/squishy-types", json={"name": "TZ Squishy"}).json()
    with Session(engine) as session:
        shipment = Shipment(wall_set_id=wall_set["id"], tracking_number="9999000000000000000001", order_ids="o1")
        session.add(shipment); session.flush()
        session.add(ShipmentRequirement(shipment_id=shipment.id, squishy_type_id=squishy["id"], quantity_required=1))
        session.commit()
        shipment_id = shipment.id

    resp = client.post(f"/api/wall-sets/{wall_set['id']}/scan", json={"barcode": squishy["internal_code"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "complete"
    with Session(engine) as session:
        assert session.get(Shipment, shipment_id).completed_at.utcoffset() == timedelta(0)
        assert session.exec(select(ScanEvent)).one().scanned_at.utcoffset() == timedelta(0)


# --- squishy type catalog ---------------------------------------------------

def test_create_and_list_squishy_types(client):
    resp = client.post("/api/squishy-types", json={"name": "Yellow Butter", "internal_code": "SQ0001"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Yellow Butter"
    assert body["internal_code"] == "SQ0001"
    assert body["is_giveaway_item"] is False

    resp = client.get("/api/squishy-types")
    assert resp.status_code == 200
    names = [t["name"] for t in resp.json()]
    assert "Yellow Butter" in names


def test_create_squishy_type_duplicate_name_rejected(client):
    client.post("/api/squishy-types", json={"name": "Dup", "internal_code": "SQ0002"})
    resp = client.post("/api/squishy-types", json={"name": "Dup", "internal_code": "SQ0003"})
    assert resp.status_code == 409


def test_create_squishy_type_without_internal_code_auto_generates_one(client):
    resp = client.post("/api/squishy-types", json={"name": "Auto Coded"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["internal_code"]  # non-empty, server-assigned

    # a second auto-coded type gets a different code than the first
    resp2 = client.post("/api/squishy-types", json={"name": "Auto Coded Two"})
    assert resp2.status_code == 200
    assert resp2.json()["internal_code"] != body["internal_code"]


def test_new_squishy_type_defaults_active(client):
    resp = client.post("/api/squishy-types", json={"name": "Fresh", "internal_code": "SQF0"})
    assert resp.json()["active"] is True


def test_deactivate_hides_type_from_default_list_but_not_include_inactive(client):
    t = client.post("/api/squishy-types", json={"name": "Seasonal", "internal_code": "SQS0"}).json()

    resp = client.post(f"/api/squishy-types/{t['id']}/deactivate")
    assert resp.status_code == 200
    assert resp.json()["active"] is False

    default_names = [x["name"] for x in client.get("/api/squishy-types").json()]
    assert "Seasonal" not in default_names

    all_names = [x["name"] for x in client.get("/api/squishy-types?include_inactive=true").json()]
    assert "Seasonal" in all_names


def test_reactivate_brings_type_back_into_default_list(client):
    t = client.post("/api/squishy-types", json={"name": "Comeback", "internal_code": "SQC0"}).json()
    client.post(f"/api/squishy-types/{t['id']}/deactivate")
    assert "Comeback" not in [x["name"] for x in client.get("/api/squishy-types").json()]

    resp = client.post(f"/api/squishy-types/{t['id']}/reactivate")
    assert resp.status_code == 200
    assert resp.json()["active"] is True
    assert "Comeback" in [x["name"] for x in client.get("/api/squishy-types").json()]


def test_deactivate_and_reactivate_are_idempotent(client):
    t = client.post("/api/squishy-types", json={"name": "Steady", "internal_code": "SQST"}).json()

    assert client.post(f"/api/squishy-types/{t['id']}/deactivate").status_code == 200
    assert client.post(f"/api/squishy-types/{t['id']}/deactivate").status_code == 200  # already inactive, no error

    assert client.post(f"/api/squishy-types/{t['id']}/reactivate").status_code == 200
    assert client.post(f"/api/squishy-types/{t['id']}/reactivate").status_code == 200  # already active, no error


def test_deactivate_and_reactivate_unknown_type_404s(client):
    assert client.post("/api/squishy-types/999999/deactivate").status_code == 404
    assert client.post("/api/squishy-types/999999/reactivate").status_code == 404


def test_create_with_name_matching_active_type_is_plain_409(client):
    client.post("/api/squishy-types", json={"name": "Still Here", "internal_code": "SQSH"})
    resp = client.post("/api/squishy-types", json={"name": "Still Here", "internal_code": "SQSH2"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "A squishy type with this name already exists"


def test_create_with_name_matching_deactivated_type_offers_reactivation(client):
    t = client.post("/api/squishy-types", json={"name": "Retired", "internal_code": "SQR0"}).json()
    client.post(f"/api/squishy-types/{t['id']}/deactivate")

    resp = client.post("/api/squishy-types", json={"name": "Retired", "internal_code": "SQR1"})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["reason"] == "inactive_duplicate"
    assert detail["squishy_type_id"] == t["id"]

    # the name still isn't usable for a genuinely new row -- the caller
    # must reactivate the existing one, not get a fresh row under the
    # same name
    reactivate = client.post(f"/api/squishy-types/{t['id']}/reactivate")
    assert reactivate.status_code == 200
    assert "Retired" in [x["name"] for x in client.get("/api/squishy-types").json()]
    # still exactly one row for this name, not two
    all_types = client.get("/api/squishy-types?include_inactive=true").json()
    assert len([x for x in all_types if x["name"] == "Retired"]) == 1


def test_deactivated_type_still_shows_correctly_in_wall_set_items_and_financials(admin_client):
    """The whole point of soft-deactivation: historical references must
    keep displaying exactly as before, only the catalog/wall-builder
    picker should change. Financials is admin-gated, so this needs
    admin_client (which is also floor-authenticated) rather than client."""
    t1 = admin_client.post("/api/squishy-types", json={"name": "Legacy Item", "internal_code": "SQL0"}).json()

    wall_set = admin_client.post("/api/wall-sets", json={
        "label": "legacy wall",
        "items": [{"squishy_type_id": t1["id"], "quantity": 4}],
    }).json()

    admin_client.post(f"/api/squishy-types/{t1['id']}/deactivate")

    # wall-set items payload still shows the real name, unaffected by deactivation
    fetched = admin_client.get(f"/api/wall-sets/{wall_set['id']}").json()
    assert fetched["items"] == [{
        "squishy_type_id": t1["id"], "name": "Legacy Item",
        "internal_code": "SQL0", "quantity": 4,
    }]

    financials_body = {
        "streamer": "Binit", "stream_started_at": "2026-01-01T00:00:00",
        "stream_ended_at": "2026-01-01T01:00:00", "revenue": 100.0, "fees": 5.0,
        "bid_average": 1.0, "giveaway_squishy_type_id": t1["id"], "giveaway_quantity": 1,
        "item_costs": [{"squishy_type_id": t1["id"], "unit_cost": 2.0}],
    }
    resp = admin_client.put(f"/api/wall-sets/{wall_set['id']}/financials", json=financials_body)
    assert resp.status_code == 200
    body = resp.json()
    assert body["giveaway"] == {"squishy_type_id": t1["id"], "name": "Legacy Item", "quantity": 1}
    assert {i["squishy_type_id"]: i["name"] for i in body["items"]} == {t1["id"]: "Legacy Item"}


# --- wall sets ---------------------------------------------------------------

def test_create_wall_set_with_items_and_fetch_it(client):
    t1 = client.post("/api/squishy-types", json={"name": "A", "internal_code": "SQA"}).json()
    t2 = client.post("/api/squishy-types", json={"name": "B", "internal_code": "SQB"}).json()

    resp = client.post("/api/wall-sets", json={
        "label": "test wall",
        "items": [
            {"squishy_type_id": t1["id"], "quantity": 3},
            {"squishy_type_id": t2["id"], "quantity": 5},
        ],
    })
    assert resp.status_code == 200
    wall_set_id = resp.json()["id"]
    assert len(resp.json()["items"]) == 2

    resp = client.get(f"/api/wall-sets/{wall_set_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "test wall"
    assert {i["squishy_type_id"] for i in body["items"]} == {t1["id"], t2["id"]}

    resp = client.get("/api/wall-sets")
    assert wall_set_id in [w["id"] for w in resp.json()]


def test_get_unknown_wall_set_404s(client):
    resp = client.get("/api/wall-sets/999")
    assert resp.status_code == 404


def test_list_shipments_returns_open_and_complete_with_nested_requirements(client, engine):
    t1 = client.post("/api/squishy-types", json={"name": "Yellow Butter", "internal_code": "SQY"}).json()
    t2 = client.post("/api/squishy-types", json={"name": "Sugar Baby", "internal_code": "SQS"}).json()
    wall_set_id = client.post("/api/wall-sets", json={"label": "shipments test"}).json()["id"]

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

    resp = client.get(f"/api/wall-sets/{wall_set_id}/shipments")
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
    wall_set = client.post("/api/wall-sets", json={"label": "empty shipments test"}).json()
    resp = client.get(f"/api/wall-sets/{wall_set['id']}/shipments")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_shipments_unknown_wall_set_404s(client):
    resp = client.get("/api/wall-sets/999/shipments")
    assert resp.status_code == 404


def test_label_sheet_download(client):
    t1 = client.post("/api/squishy-types", json={"name": "A", "internal_code": "SQA"}).json()
    wall_set = client.post("/api/wall-sets", json={
        "label": "sheet test",
        "items": [{"squishy_type_id": t1["id"], "quantity": 2}],
    }).json()

    resp = client.get(f"/api/wall-sets/{wall_set['id']}/label-sheet")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert len(resp.content) > 0


def test_squishy_type_label_sheet_download(client):
    t1 = client.post("/api/squishy-types", json={"name": "Solo Type", "internal_code": "SQSOLO"}).json()

    resp = client.get(f"/api/squishy-types/{t1['id']}/label-sheet")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    doc = fitz.open(stream=resp.content, filetype="pdf")
    assert len(doc) == 1  # default quantity
    doc.close()

    resp = client.get(f"/api/squishy-types/{t1['id']}/label-sheet", params={"quantity": 3})
    assert resp.status_code == 200
    doc = fitz.open(stream=resp.content, filetype="pdf")
    assert len(doc) == 3
    for page in doc:
        assert (page.rect.width, page.rect.height) == (216.0, 72.0)  # 3in x 1in
    doc.close()


def test_squishy_type_label_sheet_unknown_type_404s(client):
    resp = client.get("/api/squishy-types/999/label-sheet")
    assert resp.status_code == 404


def test_squishy_type_label_sheet_rejects_zero_quantity(client):
    t1 = client.post("/api/squishy-types", json={"name": "Zero Qty", "internal_code": "SQZERO"}).json()
    resp = client.get(f"/api/squishy-types/{t1['id']}/label-sheet", params={"quantity": 0})
    assert resp.status_code == 400


# --- upload / ingestion ------------------------------------------------------

@pytest.mark.skipif(not HAS_SAMPLE_DATA, reason="requires a real export in sample_data/")
def test_upload_produces_ingestion_summary(client):
    _seed_catalog_from_csv(client, CSV_PATH)
    wall_set = client.post("/api/wall-sets", json={"label": "upload test"}).json()

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
    wall_set = client.post("/api/wall-sets", json={"label": "incomplete catalog test"}).json()
    resp = _upload_sample_data(client, wall_set["id"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["requirements_created"] == 0
    assert len(body["unmatched_products"]) > 0


@pytest.mark.skipif(not HAS_SAMPLE_DATA, reason="requires a real export in sample_data/")
def test_upload_to_new_wall_set_auto_creates_wall_set_with_no_manifest(client):
    """Wall Builder no longer has a manual "build a wall" step -- uploading
    a CSV/PDF straight away, with no pre-existing WallSet, should still
    ingest correctly by creating its own WallSet on the fly."""
    _seed_catalog_from_csv(client, CSV_PATH)

    with open(CSV_PATH, "rb") as csv_f, open(PDF_PATH, "rb") as pdf_f:
        resp = client.post(
            "/api/wall-sets/upload",
            files={
                "csv_file": ("orders.csv", csv_f, "text/csv"),
                "pdf_file": ("labels.pdf", pdf_f, "application/pdf"),
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["shipments_created"] > 0
    assert body["requirements_created"] > 0
    assert body["labels_matched"] == body["shipments_created"]
    assert body["wall_set_id"] is not None
    assert body["wall_set_label"].startswith("Upload ")

    wall_set = client.get(f"/api/wall-sets/{body['wall_set_id']}").json()
    assert wall_set["orders_uploaded"] is True
    assert wall_set["items"] == []  # no manifest was ever built for this wall set

    shipments = client.get(f"/api/wall-sets/{body['wall_set_id']}/shipments").json()
    assert len(shipments) == body["shipments_created"]


# --- wall set rename ---------------------------------------------------------

def test_rename_wall_set_updates_label_and_persists(client):
    wall_set = client.post("/api/wall-sets", json={"label": "Original name"}).json()

    resp = client.patch(f"/api/wall-sets/{wall_set['id']}", json={"label": "Friday drop"})
    assert resp.status_code == 200
    assert resp.json()["label"] == "Friday drop"

    # Persists: a fresh GET and the list both reflect the new name.
    assert client.get(f"/api/wall-sets/{wall_set['id']}").json()["label"] == "Friday drop"
    listed = client.get("/api/wall-sets").json()
    assert next(w for w in listed if w["id"] == wall_set["id"])["label"] == "Friday drop"


def test_rename_wall_set_trims_and_rejects_blank(client):
    wall_set = client.post("/api/wall-sets", json={"label": "keep me"}).json()

    assert client.patch(f"/api/wall-sets/{wall_set['id']}", json={"label": "  spaced  "}).json()["label"] == "spaced"

    resp = client.patch(f"/api/wall-sets/{wall_set['id']}", json={"label": "   "})
    assert resp.status_code == 400
    # A rejected blank must not overwrite the last good value.
    assert client.get(f"/api/wall-sets/{wall_set['id']}").json()["label"] == "spaced"


def test_rename_unknown_wall_set_404s(client):
    assert client.patch("/api/wall-sets/999999", json={"label": "x"}).status_code == 404


def test_rename_wall_set_requires_floor_auth(unauthenticated_client):
    assert unauthenticated_client.patch("/api/wall-sets/1", json={"label": "x"}).status_code == 401


# --- upload type breakdown ---------------------------------------------------

def _tiny_pdf_bytes():
    """A one-page blank PDF -- enough for the upload route (label matching
    just finds nothing), so the breakdown can be tested without the real
    sample export."""
    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def test_upload_summary_includes_type_breakdown_aggregated_and_sorted(client):
    for name in ("Alpha", "Beta"):
        assert client.post("/api/squishy-types", json={"name": name}).status_code == 200

    # Alpha: 2 (T1) + 3 (T2) = 5 across two shipments; Beta: 1 (T1).
    csv_bytes = (
        "Order ID,Product Name,Quantity,Tracking ID\r\n"
        "o1,Alpha,2,T1\r\n"
        "o1,Beta,1,T1\r\n"
        "o2,Alpha,3,T2\r\n"
    ).encode("utf-8")
    resp = client.post("/api/wall-sets/upload", files={
        "csv_file": ("orders.csv", csv_bytes, "text/csv"),
        "pdf_file": ("labels.pdf", _tiny_pdf_bytes(), "application/pdf"),
    })
    assert resp.status_code == 200
    breakdown = resp.json()["type_breakdown"]
    # Aggregated across shipments, sorted by quantity descending.
    assert [(b["name"], b["total_quantity"]) for b in breakdown] == [("Alpha", 5), ("Beta", 1)]
    assert all(isinstance(b["squishy_type_id"], int) for b in breakdown)


# --- scanning ------------------------------------------------------------

@pytest.mark.skipif(not HAS_SAMPLE_DATA, reason="requires a real export in sample_data/")
def test_scan_to_complete_and_download_label(client):
    catalog = _seed_catalog_from_csv(client, CSV_PATH)
    wall_set = client.post("/api/wall-sets", json={"label": "scan test"}).json()
    wall_set_id = wall_set["id"]

    _upload_sample_data(client, wall_set_id)

    resp = client.post(f"/api/wall-sets/{wall_set_id}/scan", json={"barcode": "NOT-A-REAL-CODE"})
    assert resp.json() == {"status": "unknown_barcode"}

    tracking, product_name = _find_single_item_order(CSV_PATH)
    assert tracking is not None, "sample export has no single-item order to test against"
    barcode = catalog[product_name]["internal_code"]

    resp = client.post(f"/api/wall-sets/{wall_set_id}/scan", json={"barcode": barcode})
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
        resp = client.post(f"/api/wall-sets/{wall_set_id}/scan", json={"barcode": barcode})
        assert resp.json()["status"] in {"in_progress", "complete"}

    resp = client.post(f"/api/wall-sets/{wall_set_id}/scan", json={"barcode": barcode})
    assert resp.json()["status"] == "no_shipment_needs_it"

    resp = client.get(f"/api/wall-sets/{wall_set_id}/shipments/{shipment_id}/label")
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
    t1 = client.post("/api/squishy-types", json={"name": "Lonely Squishy", "internal_code": "SQLONE"}).json()
    wall_set = client.post("/api/wall-sets", json={
        "label": "empty scan test",
        "items": [{"squishy_type_id": t1["id"], "quantity": 1}],
    }).json()

    resp = client.post(f"/api/wall-sets/{wall_set['id']}/scan", json={"barcode": "SQLONE"})
    assert resp.json()["status"] == "no_shipment_needs_it"


def test_scan_in_progress_includes_structured_remaining_items(client, engine):
    """The frontend builds its own localized "still needs" sentence from
    `remaining` rather than parsing the English `message` -- squishy type
    names must come through untranslated, so this checks the raw name/qty
    pairs, not any particular wording."""
    t1 = client.post("/api/squishy-types", json={"name": "Yellow Butter", "internal_code": "SQY"}).json()
    t2 = client.post("/api/squishy-types", json={"name": "Sugar Baby", "internal_code": "SQS"}).json()
    wall_set_id = client.post("/api/wall-sets", json={"label": "remaining items test"}).json()["id"]

    with Session(engine) as session:
        shipment = Shipment(wall_set_id=wall_set_id, tracking_number="BUNDLE001", order_ids="O1")
        session.add(shipment)
        session.flush()
        session.add(ShipmentRequirement(
            shipment_id=shipment.id, squishy_type_id=t1["id"], quantity_required=2, quantity_scanned=0,
        ))
        session.add(ShipmentRequirement(
            shipment_id=shipment.id, squishy_type_id=t2["id"], quantity_required=1, quantity_scanned=0,
        ))
        session.commit()

    resp = client.post(f"/api/wall-sets/{wall_set_id}/scan", json={"barcode": "SQY"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "in_progress"
    remaining_by_name = {r["name"]: r["quantity_remaining"] for r in body["remaining"]}
    assert remaining_by_name == {"Yellow Butter": 1, "Sugar Baby": 1}


def test_scan_completes_multi_item_bundle_via_sequential_scans(client, engine):
    """A 3-distinct-type bundle, scanned one type at a time through the
    actual /scan route (not seeded as already-scanned) -- the third and
    final scan must return "complete" with a label, not get stuck at
    "in_progress" or regress to "no_shipment_needs_it". Guards against the
    structured `remaining` list (added for the language switcher) ever
    interfering with the completion check it sits right next to."""
    t1 = client.post("/api/squishy-types", json={"name": "Bundle Item A", "internal_code": "SQBA"}).json()
    t2 = client.post("/api/squishy-types", json={"name": "Bundle Item B", "internal_code": "SQBB"}).json()
    t3 = client.post("/api/squishy-types", json={"name": "Bundle Item C", "internal_code": "SQBC"}).json()
    wall_set_id = client.post("/api/wall-sets", json={"label": "bundle completion test"}).json()["id"]

    with Session(engine) as session:
        shipment = Shipment(wall_set_id=wall_set_id, tracking_number="BUNDLECOMPLETE001", order_ids="O1")
        session.add(shipment)
        session.flush()
        for t in (t1, t2, t3):
            session.add(ShipmentRequirement(
                shipment_id=shipment.id, squishy_type_id=t["id"], quantity_required=1, quantity_scanned=0,
            ))
        session.commit()

    assert client.post(f"/api/wall-sets/{wall_set_id}/scan", json={"barcode": "SQBA"}).json()["status"] == "in_progress"
    assert client.post(f"/api/wall-sets/{wall_set_id}/scan", json={"barcode": "SQBB"}).json()["status"] == "in_progress"

    resp = client.post(f"/api/wall-sets/{wall_set_id}/scan", json={"barcode": "SQBC"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "complete"
    assert body["tracking_number"] == "BUNDLECOMPLETE001"
    # a bundle took more than one scan to finish -- it must have a bin, and
    # the frontend needs every item that made up the shipment to build its
    # "Bin N complete: ..." message (squishy names untranslated, verbatim)
    assert body["bin_number"] is not None
    items_by_name = {i["name"]: i["quantity"] for i in body["items"]}
    assert items_by_name == {"Bundle Item A": 1, "Bundle Item B": 1, "Bundle Item C": 1}

    # persisted as complete, not just reflected in the response
    shipments = client.get(f"/api/wall-sets/{wall_set_id}/shipments").json()
    completed = next(s for s in shipments if s["tracking_number"] == "BUNDLECOMPLETE001")
    assert completed["is_complete"] is True
    assert all(r["quantity_scanned"] == r["quantity_required"] for r in completed["requirements"])


def test_scan_completes_single_item_order_with_no_bin_assigned(client, engine):
    """A single-item order completes on its one and only scan -- it never
    sits waiting in a bin, so bin_number must stay null (not get assigned
    and then immediately orphaned). This is what the frontend's bin_number
    check leans on to tell a single-item completion apart from a bundle's."""
    t1 = client.post("/api/squishy-types", json={"name": "Solo Item", "internal_code": "SQSOLO"}).json()
    wall_set_id = client.post("/api/wall-sets", json={"label": "single item completion test"}).json()["id"]

    with Session(engine) as session:
        shipment = Shipment(wall_set_id=wall_set_id, tracking_number="SOLOCOMPLETE001", order_ids="O1")
        session.add(shipment)
        session.flush()
        session.add(ShipmentRequirement(
            shipment_id=shipment.id, squishy_type_id=t1["id"], quantity_required=1, quantity_scanned=0,
        ))
        session.commit()

    resp = client.post(f"/api/wall-sets/{wall_set_id}/scan", json={"barcode": "SQSOLO"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "complete"
    assert body["bin_number"] is None
    assert body["items"] == [{"name": "Solo Item", "quantity": 1}]


def test_scan_is_scoped_to_its_own_wall_set(client, engine):
    """Not a bug, but easy to trip over now that uploads auto-create their
    own WallSet: a shipment is only reachable through the wall_set_id it
    actually belongs to. Scanning a barcode against the wrong wall set
    returns "no_shipment_needs_it" even when some other wall set has an
    open shipment that needs exactly that item."""
    t1 = client.post("/api/squishy-types", json={"name": "Cross Wall Item", "internal_code": "SQCW"}).json()
    wall_set_a = client.post("/api/wall-sets", json={"label": "wall a"}).json()["id"]
    wall_set_b = client.post("/api/wall-sets", json={"label": "wall b"}).json()["id"]

    with Session(engine) as session:
        shipment = Shipment(wall_set_id=wall_set_a, tracking_number="CROSSWALL001", order_ids="O1")
        session.add(shipment)
        session.flush()
        session.add(ShipmentRequirement(
            shipment_id=shipment.id, squishy_type_id=t1["id"], quantity_required=1, quantity_scanned=0,
        ))
        session.commit()

    resp = client.post(f"/api/wall-sets/{wall_set_b}/scan", json={"barcode": "SQCW"})
    assert resp.json()["status"] == "no_shipment_needs_it"

    resp = client.post(f"/api/wall-sets/{wall_set_a}/scan", json={"barcode": "SQCW"})
    assert resp.json()["status"] == "complete"


# --- financials (admin-only) -------------------------------------------------

def _make_wall_set_with_items(client, label="financials test"):
    # names/codes vary by label so this helper can be called more than once
    # in the same test (e.g. two wall sets) without a 409 name collision
    t1 = client.post("/api/squishy-types", json={
        "name": f"Yellow Butter ({label})", "internal_code": f"SQFY-{label}",
    }).json()
    t2 = client.post("/api/squishy-types", json={
        "name": f"Sugar Baby ({label})", "internal_code": f"SQFS-{label}", "is_giveaway_item": True,
    }).json()
    wall_set = client.post("/api/wall-sets", json={
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
    assert client.put(f"/api/wall-sets/{wall_set['id']}/financials", json=body).status_code == 401
    assert client.get(f"/api/wall-sets/{wall_set['id']}/financials").status_code == 401
    assert client.get("/api/financials").status_code == 401


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
    resp = admin_client.put(f"/api/wall-sets/{wall_set['id']}/financials", json=create_body)
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
    resp = admin_client.put(f"/api/wall-sets/{wall_set['id']}/financials", json=update_body)
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
    resp = admin_client.put(f"/api/wall-sets/{wall_set['id']}/financials", json=body)
    assert resp.status_code == 200
    created = resp.json()
    assert created["total_item_cost"] == 0.0
    assert created["roi"] is None
    assert created["profit"] == 475.0  # revenue - fees, no item cost yet

    # read it back fresh too, not just the write response
    resp = admin_client.get(f"/api/wall-sets/{wall_set['id']}/financials")
    assert resp.status_code == 200
    fetched = resp.json()
    assert fetched["total_item_cost"] == 0.0
    assert fetched["roi"] is None


def test_financials_work_for_wall_set_with_no_manifest(admin_client):
    """Wall sets created by the upload flow now have no WallSetItem manifest
    at all (not just uncosted items) -- confirms Financials still works
    sensibly against one: no item-cost rows, zero cost, null ROI, no error."""
    wall_set = admin_client.post("/api/wall-sets", json={"label": "Upload 2026-09-01 12:00:00 UTC"}).json()

    body = {
        "streamer": "Binit",
        "stream_started_at": "2026-09-01T18:00:00",
        "stream_ended_at": "2026-09-01T20:00:00",
        "revenue": 300.0, "fees": 10.0, "bid_average": 3.0,
        "item_costs": [],
    }
    resp = admin_client.put(f"/api/wall-sets/{wall_set['id']}/financials", json=body)
    assert resp.status_code == 200
    created = resp.json()
    assert created["items"] == []
    assert created["total_item_cost"] == 0.0
    assert created["roi"] is None
    assert created["profit"] == 290.0

    resp = admin_client.get("/api/financials")
    assert resp.status_code == 200
    summary = next(r for r in resp.json() if r["wall_set_id"] == wall_set["id"])
    assert summary["roi"] is None


def test_get_financials_for_wall_set_without_record_404s(admin_client):
    wall_set, _, _ = _make_wall_set_with_items(admin_client)
    resp = admin_client.get(f"/api/wall-sets/{wall_set['id']}/financials")
    assert resp.status_code == 404


def test_upsert_financials_unknown_wall_set_404s(admin_client):
    resp = admin_client.put("/api/wall-sets/999/financials", json={
        "streamer": "Binit", "stream_started_at": "2026-09-01T18:00:00",
        "stream_ended_at": "2026-09-01T20:00:00", "revenue": 1, "fees": 0,
        "bid_average": 0, "item_costs": [],
    })
    assert resp.status_code == 404


def test_upsert_financials_invalid_giveaway_type_400s(admin_client):
    wall_set, _, _ = _make_wall_set_with_items(admin_client)
    resp = admin_client.put(f"/api/wall-sets/{wall_set['id']}/financials", json={
        "streamer": "Binit", "stream_started_at": "2026-09-01T18:00:00",
        "stream_ended_at": "2026-09-01T20:00:00", "revenue": 1, "fees": 0,
        "bid_average": 0, "giveaway_squishy_type_id": 999, "item_costs": [],
    })
    assert resp.status_code == 400


def test_list_financials_summarizes_all_records(admin_client):
    wall_set_a, t1a, _ = _make_wall_set_with_items(admin_client, label="stream A")
    wall_set_b, t1b, _ = _make_wall_set_with_items(admin_client, label="stream B")

    admin_client.put(f"/api/wall-sets/{wall_set_a['id']}/financials", json={
        "streamer": "Binit", "stream_started_at": "2026-09-01T18:00:00",
        "stream_ended_at": "2026-09-01T20:00:00", "revenue": 100.0, "fees": 10.0,
        "bid_average": 1.0, "item_costs": [{"squishy_type_id": t1a["id"], "unit_cost": 1.0}],
    })
    admin_client.put(f"/api/wall-sets/{wall_set_b['id']}/financials", json={
        "streamer": "Binit", "stream_started_at": "2026-09-02T18:00:00",
        "stream_ended_at": "2026-09-02T20:00:00", "revenue": 200.0, "fees": 20.0,
        "bid_average": 1.0, "item_costs": [],
    })

    resp = admin_client.get("/api/financials")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    by_wall_set = {r["wall_set_id"]: r for r in body}
    assert by_wall_set[wall_set_a["id"]]["wall_set_label"] == "stream A"
    assert by_wall_set[wall_set_a["id"]]["roi"] is not None
    assert by_wall_set[wall_set_b["id"]]["wall_set_label"] == "stream B"
    assert by_wall_set[wall_set_b["id"]]["roi"] is None  # no item costs entered
