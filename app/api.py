"""
FastAPI wrapper around the Phase 1 engine. No business logic lives here --
every route just validates input, calls into models/order_ingest/
matching_engine/barcode_gen/label_export, and shapes the response.

Two independent, non-stacking auth tiers -- see app/auth.py's module
docstring for the full reasoning. `router` holds auth routes (both tiers)
and the admin-only financials routes; `floor_router` holds Wall Builder /
Packer Scan / Shipments, gated as a whole via its `dependencies=`.
"""
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlmodel import Session, select

from .database import get_session, init_db
from . import storage
from .auth import (
    clear_session_cookie, client_ip, create_floor_session, create_session,
    delete_session, get_admin_password_hash, require_admin, require_floor_access,
    set_floor_session_cookie, set_session_cookie, verify_floor_pin, verify_password,
    COOKIE_NAME,
)
from .barcode_gen import generate_label_sheet_pdf
from .label_export import extract_label_and_packing_slip
from .matching_engine import scan_item
from .models import (
    FinancialRecord, FinancialRecordItemCost, Shipment, ShipmentRequirement,
    SquishyType, WallSet, WallSetItem,
)
from .order_ingest import attach_label_pages, parse_csv_into_shipments

# Explicit path, not cwd-based discovery -- uvicorn can be launched from
# anywhere (a different terminal cwd, a process manager, etc.) and this
# must find the same .env regardless.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Squishy WMS", lifespan=lifespan)

# Every API route lives under /api -- the frontend's page routes (/scan,
# /financials, /login, ...) and the API's own routes (GET /financials,
# POST /wall-sets, ...) would otherwise collide on identical bare paths
# once frontend and backend are served from the same origin (the local
# Vite proxy, and the single Render service in production). No CORS
# middleware either: with a same-origin proxy locally and a single
# service in production, nothing ever makes a cross-origin request
# anymore, so there's no cross-origin case to configure for.
router = APIRouter(prefix="/api")

# Every Wall Builder / Packer Scan / Shipments route goes on this router
# instead -- gated as a whole via `dependencies=`, so "is every one of
# these actually behind the floor PIN" is answerable by which router a
# route is declared on, not by checking each route by hand for a
# decorator that might have been missed.
floor_router = APIRouter(prefix="/api", dependencies=[Depends(require_floor_access)])


# --- request/response bodies -------------------------------------------------

class SquishyTypeCreate(BaseModel):
    name: str
    internal_code: Optional[str] = None
    is_giveaway_item: bool = False


class WallSetItemCreate(BaseModel):
    squishy_type_id: int
    quantity: int


class WallSetCreate(BaseModel):
    label: str
    items: list[WallSetItemCreate] = []


class ScanRequest(BaseModel):
    barcode: str


class LoginRequest(BaseModel):
    password: str


class FloorLoginRequest(BaseModel):
    pin: str


class ItemCostInput(BaseModel):
    squishy_type_id: int
    unit_cost: float


class FinancialRecordUpsert(BaseModel):
    streamer: str
    stream_started_at: datetime
    stream_ended_at: datetime
    revenue: float
    fees: float
    bid_average: float
    giveaway_squishy_type_id: Optional[int] = None
    giveaway_quantity: Optional[int] = None
    notes: Optional[str] = None
    item_costs: list[ItemCostInput] = []


# --- auth ------------------------------------------------------------------

@router.post("/auth/login")
def login(body: LoginRequest, response: Response, session: Session = Depends(get_session)):
    if not verify_password(body.password, get_admin_password_hash()):
        raise HTTPException(status_code=401, detail="Incorrect password")

    admin_session = create_session(session)
    set_session_cookie(response, admin_session.token)
    return {"authenticated": True}


@router.post("/auth/logout")
def logout(request: Request, response: Response, session: Session = Depends(get_session)):
    token = request.cookies.get(COOKIE_NAME)
    if token is not None:
        delete_session(session, token)
    clear_session_cookie(response)
    return {"authenticated": False}


@router.get("/auth/me")
def me(admin_session=Depends(require_admin)):
    return {"authenticated": True}


@router.post("/auth/floor-login")
def floor_login(
    body: FloorLoginRequest, request: Request, response: Response,
    session: Session = Depends(get_session),
):
    if not verify_floor_pin(session, client_ip(request), body.pin):
        # Deliberately identical whether the PIN was wrong or the request
        # arrived inside the current backoff delay -- see verify_floor_pin.
        raise HTTPException(status_code=401, detail="Incorrect PIN")

    floor_session = create_floor_session(session)
    set_floor_session_cookie(response, floor_session.token)
    return {"authenticated": True}


@router.get("/auth/floor-me")
def floor_me(floor_session=Depends(require_floor_access)):
    return {"authenticated": True}


def _wall_set_items_payload(session: Session, wall_set_id: int) -> list[dict]:
    rows = session.exec(
        select(WallSetItem, SquishyType)
        .join(SquishyType, WallSetItem.squishy_type_id == SquishyType.id)
        .where(WallSetItem.wall_set_id == wall_set_id)
    ).all()
    return [
        {
            "squishy_type_id": squishy_type.id,
            "name": squishy_type.name,
            "internal_code": squishy_type.internal_code,
            "quantity": item.quantity,
        }
        for item, squishy_type in rows
    ]


def _get_wall_set_or_404(session: Session, wall_set_id: int) -> WallSet:
    wall_set = session.get(WallSet, wall_set_id)
    if wall_set is None:
        raise HTTPException(status_code=404, detail="Wall set not found")
    return wall_set


# --- squishy types -------------------------------------------------------

def _next_internal_code(session: Session) -> str:
    """SQ%04d off the next SquishyType id, for callers (the Wall Builder UI)
    that don't ask the user to pick a code by hand."""
    max_id = session.exec(select(SquishyType.id).order_by(SquishyType.id.desc())).first()
    return f"SQ{(max_id or 0) + 1:04d}"


@floor_router.post("/squishy-types")
def create_squishy_type(body: SquishyTypeCreate, session: Session = Depends(get_session)):
    existing = session.exec(
        select(SquishyType).where(SquishyType.name == body.name)
    ).first()
    if existing:
        if existing.active:
            raise HTTPException(status_code=409, detail="A squishy type with this name already exists")
        # The name is still reserved (unique) by the deactivated row --
        # don't let this fail as a generic conflict with no way forward.
        # Distinguishable detail shape so the frontend can offer a
        # reactivate action instead of a dead-end error.
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "inactive_duplicate",
                "squishy_type_id": existing.id,
                "message": f'"{body.name}" already exists, deactivated.',
            },
        )

    squishy_type = SquishyType(
        name=body.name,
        internal_code=body.internal_code or _next_internal_code(session),
        is_giveaway_item=body.is_giveaway_item,
    )
    session.add(squishy_type)
    session.commit()
    session.refresh(squishy_type)
    return squishy_type


@floor_router.get("/squishy-types")
def list_squishy_types(include_inactive: bool = False, session: Session = Depends(get_session)):
    query = select(SquishyType).order_by(SquishyType.name)
    if not include_inactive:
        query = query.where(SquishyType.active == True)  # noqa: E712 -- SQLAlchemy needs `== True`, not `is True`
    return session.exec(query).all()


@floor_router.post("/squishy-types/{squishy_type_id}/deactivate")
def deactivate_squishy_type(squishy_type_id: int, session: Session = Depends(get_session)):
    """Removes a type from the active catalog/wall-builder picker without
    touching its data -- every historical Shipment/ScanEvent/
    FinancialRecord reference keeps resolving through it exactly as
    before (see order_ingest.py and matching_engine.py, neither of which
    filters by `active`). Idempotent: deactivating an already-inactive
    type just returns its current state, no error."""
    squishy_type = session.get(SquishyType, squishy_type_id)
    if squishy_type is None:
        raise HTTPException(status_code=404, detail="Squishy type not found")
    squishy_type.active = False
    session.add(squishy_type)
    session.commit()
    session.refresh(squishy_type)
    return squishy_type


@floor_router.post("/squishy-types/{squishy_type_id}/reactivate")
def reactivate_squishy_type(squishy_type_id: int, session: Session = Depends(get_session)):
    squishy_type = session.get(SquishyType, squishy_type_id)
    if squishy_type is None:
        raise HTTPException(status_code=404, detail="Squishy type not found")
    squishy_type.active = True
    session.add(squishy_type)
    session.commit()
    session.refresh(squishy_type)
    return squishy_type


@floor_router.get("/squishy-types/{squishy_type_id}/label-sheet")
def download_squishy_type_label_sheet(
    squishy_type_id: int, quantity: int = 1, session: Session = Depends(get_session)
):
    squishy_type = session.get(SquishyType, squishy_type_id)
    if squishy_type is None:
        raise HTTPException(status_code=404, detail="Squishy type not found")
    if quantity < 1:
        raise HTTPException(status_code=400, detail="quantity must be at least 1")

    pdf_bytes = generate_label_sheet_pdf([{
        "internal_code": squishy_type.internal_code,
        "display_name": squishy_type.name,
        "quantity": quantity,
    }])
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{squishy_type.internal_code}.pdf"'},
    )


# --- wall sets -------------------------------------------------------------

@floor_router.post("/wall-sets")
def create_wall_set(body: WallSetCreate, session: Session = Depends(get_session)):
    wall_set = WallSet(label=body.label)
    session.add(wall_set)
    session.flush()  # get wall_set.id before adding items

    for item in body.items:
        squishy_type = session.get(SquishyType, item.squishy_type_id)
        if squishy_type is None:
            raise HTTPException(status_code=400, detail=f"Unknown squishy_type_id {item.squishy_type_id}")
        session.add(WallSetItem(
            wall_set_id=wall_set.id,
            squishy_type_id=item.squishy_type_id,
            quantity=item.quantity,
        ))

    session.commit()
    session.refresh(wall_set)
    return {**wall_set.model_dump(), "items": _wall_set_items_payload(session, wall_set.id)}


@floor_router.get("/wall-sets")
def list_wall_sets(session: Session = Depends(get_session)):
    return session.exec(select(WallSet).order_by(WallSet.created_at.desc())).all()


@floor_router.get("/wall-sets/{wall_set_id}")
def get_wall_set(wall_set_id: int, session: Session = Depends(get_session)):
    wall_set = _get_wall_set_or_404(session, wall_set_id)
    return {**wall_set.model_dump(), "items": _wall_set_items_payload(session, wall_set_id)}


@floor_router.get("/wall-sets/{wall_set_id}/shipments")
def list_shipments(wall_set_id: int, session: Session = Depends(get_session)):
    _get_wall_set_or_404(session, wall_set_id)

    shipments = session.exec(
        select(Shipment).where(Shipment.wall_set_id == wall_set_id).order_by(Shipment.created_at)
    ).all()

    requirements_by_shipment: dict[int, list[dict]] = {}
    if shipments:
        requirement_rows = session.exec(
            select(ShipmentRequirement, SquishyType)
            .join(SquishyType, ShipmentRequirement.squishy_type_id == SquishyType.id)
            .where(ShipmentRequirement.shipment_id.in_([s.id for s in shipments]))
        ).all()
        for requirement, squishy_type in requirement_rows:
            requirements_by_shipment.setdefault(requirement.shipment_id, []).append({
                "squishy_type_id": squishy_type.id,
                "name": squishy_type.name,
                "quantity_required": requirement.quantity_required,
                "quantity_scanned": requirement.quantity_scanned,
            })

    return [
        {
            "id": shipment.id,
            "tracking_number": shipment.tracking_number,
            "order_ids": shipment.order_ids,
            "bin_number": shipment.bin_number,
            "is_complete": shipment.is_complete,
            "completed_at": shipment.completed_at,
            "requirements": requirements_by_shipment.get(shipment.id, []),
        }
        for shipment in shipments
    ]


@floor_router.get("/wall-sets/{wall_set_id}/label-sheet")
def download_label_sheet(wall_set_id: int, session: Session = Depends(get_session)):
    _get_wall_set_or_404(session, wall_set_id)
    items = _wall_set_items_payload(session, wall_set_id)
    if not items:
        raise HTTPException(status_code=400, detail="Wall set has no items to print labels for")

    pdf_bytes = generate_label_sheet_pdf(
        [{"internal_code": i["internal_code"], "display_name": i["name"], "quantity": i["quantity"]} for i in items],
    )
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="label_sheet.pdf"'},
    )


async def _ingest_wall_set_upload(
    session: Session, wall_set: WallSet, csv_bytes: bytes, pdf_bytes: bytes,
) -> dict:
    pdf_key = storage.save_wall_set_upload(wall_set.id, csv_bytes, pdf_bytes)

    ingest_summary = parse_csv_into_shipments(csv_bytes, wall_set.id, session)
    labels_matched = attach_label_pages(session, wall_set.id, pdf_bytes)

    wall_set.pdf_file_path = pdf_key
    wall_set.orders_uploaded = True
    session.add(wall_set)
    session.commit()

    return {
        "shipments_created": ingest_summary["shipments_created"],
        "requirements_created": ingest_summary["requirements_created"],
        "unmatched_products": ingest_summary["unmatched_products"],
        "labels_matched": labels_matched,
    }


@floor_router.post("/wall-sets/{wall_set_id}/upload")
async def upload_orders(
    wall_set_id: int,
    csv_file: UploadFile = File(...),
    pdf_file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    wall_set = _get_wall_set_or_404(session, wall_set_id)
    return await _ingest_wall_set_upload(
        session, wall_set, await csv_file.read(), await pdf_file.read(),
    )


@floor_router.post("/wall-sets/upload")
async def upload_orders_new_wall_set(
    csv_file: UploadFile = File(...),
    pdf_file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    """Auto-creates a WallSet with no manifest, labeled by upload time --
    for uploads that never went through a manual wall-build step. Every
    manifest-quantity reader (Financials' item-cost table, the per-wall-set
    label sheet) already treats an empty WallSetItem list as "nothing to
    show", not an error, so this needs no other changes."""
    label = f"Upload {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    wall_set = WallSet(label=label)
    session.add(wall_set)
    session.flush()  # get wall_set.id before storage paths are computed

    summary = await _ingest_wall_set_upload(
        session, wall_set, await csv_file.read(), await pdf_file.read(),
    )
    return {**summary, "wall_set_id": wall_set.id, "wall_set_label": wall_set.label}


@floor_router.post("/wall-sets/{wall_set_id}/scan")
def scan(wall_set_id: int, body: ScanRequest, session: Session = Depends(get_session)):
    _get_wall_set_or_404(session, wall_set_id)

    squishy_type = session.exec(
        select(SquishyType).where(SquishyType.internal_code == body.barcode)
    ).first()
    if squishy_type is None:
        return {"status": "unknown_barcode"}

    result = scan_item(session, wall_set_id, squishy_type.id)

    if not result.matched:
        return {"status": "no_shipment_needs_it", "message": result.message}

    if result.shipment_complete:
        return {
            "status": "complete",
            "shipment_id": result.shipment_id,
            "tracking_number": result.tracking_number,
            "bin_number": result.bin_number,
            "message": result.message,
            "items": [{"name": i.name, "quantity": i.quantity} for i in result.items],
        }

    return {
        "status": "in_progress",
        "shipment_id": result.shipment_id,
        "bin_number": result.bin_number,
        "message": result.message,
        "remaining": [
            {"name": r.name, "quantity_remaining": r.quantity_remaining}
            for r in result.remaining
        ],
    }


@floor_router.get("/wall-sets/{wall_set_id}/shipments/{shipment_id}/label")
def download_shipment_label(wall_set_id: int, shipment_id: int, session: Session = Depends(get_session)):
    wall_set = _get_wall_set_or_404(session, wall_set_id)
    shipment = session.get(Shipment, shipment_id)
    if shipment is None or shipment.wall_set_id != wall_set_id:
        raise HTTPException(status_code=404, detail="Shipment not found")
    if shipment.pdf_label_page_index is None or wall_set.pdf_file_path is None:
        raise HTTPException(status_code=400, detail="No label page available for this shipment yet")

    master_pdf_bytes = storage.read_pdf(wall_set.pdf_file_path)
    pdf_bytes = extract_label_and_packing_slip(master_pdf_bytes, shipment.pdf_label_page_index)
    return Response(content=pdf_bytes, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{shipment.tracking_number}.pdf"'
    })


# --- financials (admin-only) ------------------------------------------------

def _financial_record_payload(session: Session, record: FinancialRecord) -> dict:
    """Item quantities come from WallSetItem, not from FinancialRecordItemCost
    -- this joins the two live so the numbers can never drift out of sync
    with the wall set's actual manifest. total_item_cost only sums items
    that have a cost entered; roi is null (not a divide-by-zero error) until
    total_item_cost is actually > 0, since Binit may record revenue/fees
    right after a stream and fill in item costs later."""
    wall_set_items = session.exec(
        select(WallSetItem, SquishyType)
        .join(SquishyType, WallSetItem.squishy_type_id == SquishyType.id)
        .where(WallSetItem.wall_set_id == record.wall_set_id)
    ).all()

    cost_by_type_id = {
        c.squishy_type_id: c.unit_cost
        for c in session.exec(
            select(FinancialRecordItemCost)
            .where(FinancialRecordItemCost.financial_record_id == record.id)
        ).all()
    }

    items = []
    total_item_cost = 0.0
    for wall_set_item, squishy_type in wall_set_items:
        unit_cost = cost_by_type_id.get(squishy_type.id)
        line_cost = unit_cost * wall_set_item.quantity if unit_cost is not None else None
        if line_cost is not None:
            total_item_cost += line_cost
        items.append({
            "squishy_type_id": squishy_type.id,
            "name": squishy_type.name,
            "quantity": wall_set_item.quantity,
            "unit_cost": unit_cost,
            "line_cost": line_cost,
        })

    profit = record.revenue - record.fees - total_item_cost
    roi = (profit / total_item_cost) if total_item_cost > 0 else None
    duration_seconds = (record.stream_ended_at - record.stream_started_at).total_seconds()

    giveaway = None
    if record.giveaway_squishy_type_id is not None:
        giveaway_type = session.get(SquishyType, record.giveaway_squishy_type_id)
        giveaway = {
            "squishy_type_id": record.giveaway_squishy_type_id,
            "name": giveaway_type.name if giveaway_type else None,
            "quantity": record.giveaway_quantity,
        }

    return {
        "id": record.id,
        "wall_set_id": record.wall_set_id,
        "streamer": record.streamer,
        "stream_started_at": record.stream_started_at,
        "stream_ended_at": record.stream_ended_at,
        "duration_seconds": duration_seconds,
        "revenue": record.revenue,
        "fees": record.fees,
        "bid_average": record.bid_average,
        "giveaway": giveaway,
        "notes": record.notes,
        "items": items,
        "total_item_cost": total_item_cost,
        "profit": profit,
        "roi": roi,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


@router.put("/wall-sets/{wall_set_id}/financials")
def upsert_financials(
    wall_set_id: int, body: FinancialRecordUpsert,
    session: Session = Depends(get_session), admin_session=Depends(require_admin),
):
    _get_wall_set_or_404(session, wall_set_id)

    if body.giveaway_squishy_type_id is not None:
        if session.get(SquishyType, body.giveaway_squishy_type_id) is None:
            raise HTTPException(status_code=400, detail="Unknown giveaway_squishy_type_id")

    record = session.exec(
        select(FinancialRecord).where(FinancialRecord.wall_set_id == wall_set_id)
    ).first()
    if record is None:
        record = FinancialRecord(wall_set_id=wall_set_id, streamer=body.streamer,
                                  stream_started_at=body.stream_started_at,
                                  stream_ended_at=body.stream_ended_at,
                                  revenue=body.revenue, fees=body.fees, bid_average=body.bid_average)

    record.streamer = body.streamer
    record.stream_started_at = body.stream_started_at
    record.stream_ended_at = body.stream_ended_at
    record.revenue = body.revenue
    record.fees = body.fees
    record.bid_average = body.bid_average
    record.giveaway_squishy_type_id = body.giveaway_squishy_type_id
    record.giveaway_quantity = body.giveaway_quantity
    record.notes = body.notes
    record.updated_at = datetime.utcnow()

    session.add(record)
    session.flush()  # ensure record.id exists before touching item costs

    for existing_cost in session.exec(
        select(FinancialRecordItemCost).where(FinancialRecordItemCost.financial_record_id == record.id)
    ).all():
        session.delete(existing_cost)
    session.flush()

    for item_cost in body.item_costs:
        session.add(FinancialRecordItemCost(
            financial_record_id=record.id,
            squishy_type_id=item_cost.squishy_type_id,
            unit_cost=item_cost.unit_cost,
        ))

    session.commit()
    session.refresh(record)
    return _financial_record_payload(session, record)


@router.get("/wall-sets/{wall_set_id}/financials")
def get_financials(
    wall_set_id: int, session: Session = Depends(get_session), admin_session=Depends(require_admin),
):
    _get_wall_set_or_404(session, wall_set_id)
    record = session.exec(
        select(FinancialRecord).where(FinancialRecord.wall_set_id == wall_set_id)
    ).first()
    if record is None:
        raise HTTPException(status_code=404, detail="No financial record for this wall set yet")
    return _financial_record_payload(session, record)


@router.get("/financials")
def list_financials(session: Session = Depends(get_session), admin_session=Depends(require_admin)):
    rows = session.exec(
        select(FinancialRecord, WallSet)
        .join(WallSet, FinancialRecord.wall_set_id == WallSet.id)
        .order_by(FinancialRecord.stream_started_at.desc())
    ).all()

    summaries = []
    for record, wall_set in rows:
        payload = _financial_record_payload(session, record)
        summaries.append({
            "wall_set_id": wall_set.id,
            "wall_set_label": wall_set.label,
            "streamer": record.streamer,
            "stream_started_at": record.stream_started_at,
            "revenue": record.revenue,
            "profit": payload["profit"],
            "roi": payload["roi"],
        })
    return summaries


app.include_router(router)
app.include_router(floor_router)

# Serves the built frontend (frontend/dist, from `npm run build`) so a
# single Render service can host both the API and the UI -- no separate
# static-hosting service, no cross-origin requests to configure. Only
# present when frontend/dist actually exists: local dev never builds it
# (Vite's own dev server + proxy serves the frontend there instead, see
# frontend/vite.config.ts), so this stays a no-op and doesn't error on a
# missing directory outside of a production-style build+run.
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if FRONTEND_DIST.is_dir():
    # Vite's hashed JS/CSS bundles -- served directly, no fallback needed
    # since the browser only ever requests exact filenames it got from
    # index.html.
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """Every page route (/scan, /financials, /login, a page reload on
        any of them, ...) is handled client-side by React Router, so any
        path that isn't a real API route just gets index.html and lets
        the frontend's own router take it from there.

        Guards against /api/* falling through to this catch-all: without
        it, a genuinely-missing API route (typo, wrong method) would
        silently return the SPA's index.html with a 200 instead of a real
        404, masking API errors as if the frontend just failed to load.
        """
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(FRONTEND_DIST / "index.html")
