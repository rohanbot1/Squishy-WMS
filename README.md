# Squishy WMS (Binit's livestream fulfillment software)

Warehouse software for Binit's squishy livestream selling operation. No
TikTok/Amazon API integration, everything is manual upload + in-house
barcodes. `app/` holds the working core (Phase 1, tested against real
production data) and its FastAPI wrapper (Phase 2, now growing into
Phase 3); `frontend/` is the React UI on top of it.

## How it works

1. Before a stream, employees build a "wall": a manifest of squishy
   types and quantities. Each type gets an in-house barcode printed on
   a label (squishies have no manufacturer barcode).
2. After the stream, Binit uploads two files: TikTok's CSV order export
   and the combined shipping-label/packing-slip PDF.
3. A packer scans each physical item. The system figures out which
   shipment (box) that item belongs to and either tells the packer
   which bin to put it in (order isn't complete yet) or prints the
   shipping label (order is complete).

## Key architecture decisions (already validated against real data)

- **Shipments are keyed by TikTok tracking number, not order ID.**
  TikTok itself combines separate orders into one box before we see the
  data. Grouping by order ID misses that combination; grouping by
  tracking number is what actually reflects one physical box needing
  one label.
- **The CSV is the source of truth for item names/quantities, never the
  PDF.** The PDF's text layer mangles emoji into null bytes (confirmed
  with two different extraction libraries). The CSV's Product Name
  column keeps emoji intact. The PDF is only used for its label page
  images, looked up by tracking number.
- **Barcodes encode an internal code, not the display name.** Label
  printer fonts generally can't render emoji. `SquishyType.internal_code`
  is what's in the barcode; `SquishyType.name` (with emoji) is a
  display/matching concern only.
- **One matching algorithm for everything.** There's no special case for
  "single item" vs "bundle" order. Every shipment is just a requirement
  list (squishy type -> qty needed). A single-item order is the case
  where that list has one entry with qty 1, so it completes on the
  first scan. This also correctly handles an order needing 2x of the
  *same* item, which doesn't fit "single vs bundle" but shows up in the
  real data.

## What's built (Phase 1)

- `app/models.py` -- SquishyType, WallSet, WallSetItem, Shipment,
  ShipmentRequirement, ScanEvent
- `app/order_ingest.py` -- parses the CSV into Shipment +
  ShipmentRequirement rows, indexes the PDF's label pages by tracking
  number
- `app/matching_engine.py` -- `scan_item()`, the core packer-facing
  function
- `app/barcode_gen.py` -- generates printable barcode label sheets
- `tests/test_with_real_files.py` -- runs the whole pipeline against a
  real CSV/PDF export and replays every scan to confirm every shipment
  completes correctly

Test result on the real file this was built against: 146 shipments
correctly grouped from 513 order lines, 73 of them multi-item bundles,
all 146 matched to a real label page, all 513 scans (replayed in random
order) resolved with zero mismatches and zero shipments left open.

## What's built (Phase 2)

FastAPI wrapper around the Phase 1 engine, plus a React wall-builder and
packer-scan UI. No business logic lives in the API layer -- every route
just validates input and calls into the existing engine modules. These
floor-facing routes are deliberately unauthenticated (see Phase 3 below
for the auth mechanism, which exists for a future admin view, not these).

- `app/api.py` -- the FastAPI app and all routes
- `app/storage.py` -- filesystem layout for uploads/generated files
  (`storage/wall_sets/<id>/manifest.csv`, `labels.pdf`, `label_sheet.pdf`)
- `app/label_export.py` -- `extract_label_and_packing_slip()`, pulls a
  shipment's label page plus the packing slip page immediately after it
  (product names, qty, order ID) out of a wall set's master PDF as a
  standalone two-page PDF (used for the per-shipment label download route)
- `WallSet.pdf_file_path` (new field on the existing model) -- points at
  the stored master PDF for that wall set, set on upload
- `frontend/` -- Vite + React + TypeScript, plain CSS, react-router with
  two screens:
  - **Wall Builder** (`/`) -- add squishy types to the catalog, build a
    wall (type + quantity), download the barcode label sheet, upload the
    CSV + PDF and see the ingestion summary with unmatched product names
    called out
  - **Packer Scan** (`/scan`) -- pick a wall set, one auto-focused input
    wired to Enter for scanner input, shows bin instructions inline,
    triggers the shipment's label PDF download the moment it completes
- `tests/test_api.py` -- FastAPI `TestClient` coverage: catalog CRUD,
  wall set creation, label sheet download, upload -> ingestion summary,
  and a full scan-to-complete-to-label-download sequence (the upload and
  scan tests reuse the real sample export and are skipped if
  `sample_data/` isn't present locally, same as `test_with_real_files.py`)

### API routes

- `POST /squishy-types`, `GET /squishy-types`
- `POST /wall-sets`, `GET /wall-sets`, `GET /wall-sets/{id}`
- `GET /wall-sets/{id}/label-sheet`
- `POST /wall-sets/{id}/upload` -- multipart `csv_file` + `pdf_file`, runs
  ingestion, returns `shipments_created` / `requirements_created` /
  `unmatched_products` / `labels_matched`
- `POST /wall-sets/{id}/scan` -- body `{barcode}`, returns one of
  `unknown_barcode` / `no_shipment_needs_it` / `in_progress` (+
  `bin_number`) / `complete` (+ `shipment_id`)
- `GET /wall-sets/{id}/shipments` -- every shipment for a wall set with
  requirements nested inline (name, quantity required/scanned per item)
- `GET /wall-sets/{id}/shipments/{shipment_id}/label` -- the shipment's
  label page + packing slip page as a two-page PDF
- `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` -- see the
  Phase 3 auth section below

CORS is open to the Vite dev origin (`http://localhost:5173`), with
`allow_credentials=True` so the session cookie rides along. SQLite via
the existing `app/database.py`; `init_db()` runs on API startup.

`storage/` is gitignored alongside `sample_data/` -- every wall set's
upload carries the same real buyer PII as the sample file.

## What's built (Phase 3)

- `GET /wall-sets/{id}/shipments` in `app/api.py` -- lists every
  shipment for a wall set with its requirements nested inline; no route
  existed before this to browse shipments at all, only to scan into
  them or fetch one label by id
- `frontend/src/pages/Shipments.tsx` (`/shipments`) -- pick a wall set,
  filter Open (default) / Complete / All, search by tracking number,
  expand a row for its per-item required/scanned breakdown, download
  the label for any completed shipment (reuses the existing
  per-shipment label route, no new download logic)
- `tests/test_api.py` -- the `client` fixture now also exposes the
  underlying test `engine` (via a new `engine` fixture) so tests can
  seed `Shipment`/`ShipmentRequirement` rows directly for deterministic
  open/complete/multi-item scenarios, instead of only being able to
  create shipments through a real CSV upload

**Auth.** There's exactly one admin (Binit) -- this is a single-password
login, not a user system, and it only exists to eventually gate the
still-unbuilt admin/financials view. Wall Builder, Packer Scan, and
Shipments stay deliberately unauthenticated; nothing about them changed.

- `app/auth.py` -- `hash_password`/`verify_password` (stdlib
  `hashlib.pbkdf2_hmac`, no new hashing dependency), session
  create/delete/cookie helpers, and `require_admin`: the reusable
  FastAPI dependency any future admin route adds via
  `Depends(require_admin)` to get the same cookie+expiry check `/auth/me`
  uses
- `AdminSession` (new table in `app/models.py`) -- DB-backed sessions
  (30-day expiry) so a login survives a server restart, not just an
  in-memory dict
- The password itself lives in the `ADMIN_PASSWORD_HASH` env var
  (`<salt_hex>:<hash_hex>`), loaded from a gitignored `.env` file via
  `python-dotenv` -- `scripts/set_admin_password.py` prompts for a
  password (hidden input) and writes that env var for you
- `frontend/src/pages/Login.tsx` (`/login`) -- password field, nothing
  else; the nav shows "Log in" or "Logged in / Logout" based on a
  `GET /auth/me` check on load
- `API_BASE` in `frontend/src/api.ts` uses hostname `localhost`, not
  `127.0.0.1` -- the session cookie is `SameSite=Lax`, which cares about
  hostname (not port), so the frontend origin and the API origin have to
  agree on "localhost" or the browser silently won't send the cookie
- The backend now runs on **port 8010**, not 8000 -- an unrelated local
  project (`fba-receiver-full`) binds `0.0.0.0:8000` and was silently
  intercepting some requests meant for this app. Update `API_BASE` (and
  the `uvicorn --port` below) if that conflict is ever resolved and you'd
  rather move back.

**Financials.** The admin-only financial/performance view -- runs
alongside Binit's manual tracking spreadsheet, doesn't replace it. Every
route is gated with `Depends(require_admin)`.

- `FinancialRecord` (new table in `app/models.py`) -- one row per
  `WallSet` (1:1, enforced via a unique FK), holding streamer, stream
  start/end, revenue, fees, bid average, an optional giveaway item +
  quantity, and notes. Item quantities are deliberately **not**
  duplicated here -- they're read live from that wall set's
  `WallSetItem` rows, since that's already the source of truth for what
  was in the wall
- `FinancialRecordItemCost` (new table) -- one row per squishy type
  costed for a given record, snapshotting a per-unit cost at save time.
  Per-unit cost is intentionally not a permanent field on `SquishyType`
  since it drifts stream to stream
- `_financial_record_payload()` in `app/api.py` -- joins those two
  tables against `WallSetItem` to compute `total_item_cost`, `profit`,
  and `roi` on every read. **`roi` is `null`, not a divide-by-zero
  error, whenever `total_item_cost` is 0** -- an expected state, since
  Binit may log revenue/fees right after a stream and fill in item
  costs later. Covered by
  `test_financials_zero_item_cost_roi_is_null` in `tests/test_api.py`
  (saves a record with no item costs, asserts `total_item_cost == 0`
  and `roi is None` on both the write response and a fresh read back)
- `PUT /wall-sets/{id}/financials` -- upsert (create if none exists yet,
  otherwise update in place; item costs are fully replaced, not merged,
  on every save), `GET /wall-sets/{id}/financials` -- one record (404 if
  none yet), `GET /financials` -- summary list of every recorded stream
  (wall set, streamer, date, revenue, profit, roi)
- `frontend/src/pages/Financials.tsx` (`/financials`) -- every recorded
  stream in a table, plus every wall set that doesn't have a record yet
  with an "Add financials" link; `roi: null` renders as "Not yet
  calculated"
- `frontend/src/pages/FinancialDetail.tsx` (`/financials/:wallSetId`) --
  one form: streamer, stream start/end, revenue, fees, bid average,
  giveaway item + quantity, notes, and a per-item cost row (name/qty
  read-only from the wall set's manifest, unit cost editable) with
  total cost / profit / ROI computed live client-side using the same
  formula as the backend; `roi === null` renders as "—". Pre-fills from
  the existing record if one exists
- Nav only shows the "Financials" link when `GET /auth/me` says the
  session is an admin -- logged-out users don't see it at all, not even
  as a dead link

## What's next (Phase 3+)

- Real label printer integration (barcode_gen and label_export just
  output PDFs today; needs research into what the Rollo actually
  exposes -- standard OS printer driver vs. a raw protocol -- before a
  design is possible)

## Local setup

This is split into two parts: **one-time setup** (do this once per
computer -- e.g. once on the warehouse machine, ever) and **daily use**
(what `start_squishy_wms.bat` automates every day after that).

### One-time setup

Install once, in order:

1. **Python 3.11+** -- [python.org](https://www.python.org/downloads/),
   check "Add python.exe to PATH" during install.
2. **Node.js 18+** -- [nodejs.org](https://nodejs.org/) (LTS build),
   which bundles npm. **Restart the computer (or at least log out and
   back in) after installing** -- Windows only picks up the PATH change
   on a fresh login, and `start_squishy_wms.bat` won't find `node`/`npm`
   until then.
3. **This repo** -- clone or copy the whole `squishy_wms` folder onto
   the machine.
4. **Backend dependencies + venv**, from the project root:
   ```
   python -m venv venv
   venv\Scripts\activate            # macOS/Linux: source venv/bin/activate
   pip install -r requirements.txt
   ```
5. **Frontend dependencies**:
   ```
   cd frontend
   npm install
   cd ..
   ```
6. **Admin password**, from the project root (venv still active):
   ```
   python scripts/set_admin_password.py
   ```
   This prompts for a password (hidden input) and writes
   `ADMIN_PASSWORD_HASH` into a gitignored `.env` file. Login won't work
   until this has been run at least once; everything else (Wall Builder,
   Packer Scan, Shipments) works fine without it, since only `/auth/*`
   and the admin-gated Financials routes need it.

Optional, for developers rather than daily warehouse use:

```
python tests/test_with_real_files.py   # requires a real CSV/PDF export, see below
pytest tests/                          # includes tests/test_api.py
```

### Daily use

Double-click **`start_squishy_wms.bat`** in the project root. It:

- checks that setup (step 2, 4, and 5 above) actually happened, and
  tells you plainly what's missing if not, instead of failing silently
- starts the backend (`uvicorn`, port 8010) and frontend (`npm run dev`,
  port 5173) each in their own window, so you can see their logs if
  something goes wrong
- waits for both to actually respond, then opens
  `http://localhost:5173` in your default browser automatically

Leave the two "Squishy WMS - Backend" / "Squishy WMS - Frontend"
windows open while using the app -- closing either one stops that half
of the app. Closing the small launcher window once the browser has
opened is fine.

To start it manually instead (what the batch file does under the hood):

```
# Terminal 1, from the project root:
venv\Scripts\activate
uvicorn app.api:app --reload --port 8010   # serves on http://127.0.0.1:8010

# Terminal 2:
cd frontend
npm run dev                                # serves on http://localhost:5173
```

`sample_data/` is gitignored on purpose: TikTok's CSV export contains
real buyer names, addresses, and phone numbers. Drop a real export
there locally to keep testing against real data, but never commit it.
`storage/` (created at runtime by the API) is gitignored for the same
reason. `.env` (holds `ADMIN_PASSWORD_HASH`) is gitignored too -- never
commit real credentials.
