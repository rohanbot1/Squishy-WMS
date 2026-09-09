// Always relative -- the backend is same-origin in every environment:
// Vite's dev-server proxy forwards /api to the backend locally (see
// vite.config.ts), and a single service serves both in production. There
// is no separate host to configure per environment.
const API_BASE = "/api";

export interface SquishyType {
  id: number;
  name: string;
  internal_code: string;
  is_giveaway_item: boolean;
  active: boolean;
  created_at: string;
}

// The shape POST /squishy-types returns as its 409 `detail` when the
// name collides with a *deactivated* type instead of an active one --
// distinguishable from the plain string `detail` on every other error so
// the caller can offer a reactivate action instead of a dead-end message.
export interface InactiveDuplicateDetail {
  reason: "inactive_duplicate";
  squishy_type_id: number;
  message: string;
}

export interface WallSetItem {
  squishy_type_id: number;
  name: string;
  internal_code: string;
  quantity: number;
}

export interface WallSet {
  id: number;
  label: string;
  created_at: string;
  orders_uploaded: boolean;
  pdf_file_path: string | null;
  items: WallSetItem[];
}

export interface UploadSummary {
  shipments_created: number;
  requirements_created: number;
  unmatched_products: string[];
  labels_matched: number;
}

export interface UploadToNewWallSetSummary extends UploadSummary {
  wall_set_id: number;
  wall_set_label: string;
}

export interface RemainingRequirement {
  name: string;
  quantity_remaining: number;
}

export interface ShipmentItem {
  name: string;
  quantity: number;
}

export interface ShipmentRequirementRow {
  squishy_type_id: number;
  name: string;
  quantity_required: number;
  quantity_scanned: number;
}

export interface ShipmentRow {
  id: number;
  tracking_number: string;
  order_ids: string;
  bin_number: number | null;
  is_complete: boolean;
  completed_at: string | null;
  requirements: ShipmentRequirementRow[];
}

export interface FinancialItemLine {
  squishy_type_id: number;
  name: string;
  quantity: number;
  unit_cost: number | null;
  line_cost: number | null;
}

export interface FinancialGiveaway {
  squishy_type_id: number;
  name: string | null;
  quantity: number | null;
}

export interface FinancialRecord {
  id: number;
  wall_set_id: number;
  streamer: string;
  stream_started_at: string;
  stream_ended_at: string;
  duration_seconds: number;
  revenue: number;
  fees: number;
  bid_average: number;
  giveaway: FinancialGiveaway | null;
  notes: string | null;
  items: FinancialItemLine[];
  total_item_cost: number;
  profit: number;
  roi: number | null; // null until total_item_cost > 0 -- render as "—", never as a raw null or NaN
  created_at: string;
  updated_at: string;
}

export interface FinancialSummary {
  wall_set_id: number;
  wall_set_label: string;
  streamer: string;
  stream_started_at: string;
  revenue: number;
  profit: number;
  roi: number | null;
}

export interface FinancialRecordUpsertBody {
  streamer: string;
  stream_started_at: string;
  stream_ended_at: string;
  revenue: number;
  fees: number;
  bid_average: number;
  giveaway_squishy_type_id?: number | null;
  giveaway_quantity?: number | null;
  notes?: string | null;
  item_costs: { squishy_type_id: number; unit_cost: number }[];
}

export type ScanResponse =
  | { status: "unknown_barcode" }
  | { status: "no_shipment_needs_it"; message: string }
  | {
      status: "in_progress";
      shipment_id: number;
      bin_number: number;
      message: string;
      remaining: RemainingRequirement[];
    }
  | {
      status: "complete";
      shipment_id: number;
      tracking_number: string;
      bin_number: number | null;
      message: string;
      items: ShipmentItem[];
    };

// Thrown by request() on any non-2xx response. `detail` carries whatever
// FastAPI's `detail=` actually was: usually a plain string, but a route
// can also return a structured object (see InactiveDuplicateDetail) for
// an error a caller needs to branch on instead of just display.
export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, statusText: string, detail: unknown) {
    const readable = typeof detail === "string" ? detail : JSON.stringify(detail);
    super(`${status} ${statusText}: ${readable}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, { credentials: "include", ...init });
  if (!resp.ok) {
    const text = await resp.text();
    let detail: unknown = text;
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === "object" && "detail" in parsed) detail = parsed.detail;
    } catch {
      // Not JSON -- keep the raw text as the detail.
    }
    throw new ApiError(resp.status, resp.statusText, detail);
  }
  return resp.json() as Promise<T>;
}

// Both tiers' "am I logged in" state (App.tsx's `isAdmin` / `isFloorUnlocked`)
// is only ever set from a mount-time check -- it never re-verifies itself,
// so it can go stale relative to the session's actual, current validity
// (expired, logged out elsewhere, etc). Admin- and floor-gated pages alike
// use this to detect that staleness from a real 401 and resync/redirect
// rather than just showing a raw error under state that still claims
// everything's fine.
export function isUnauthorizedError(e: unknown): boolean {
  return e instanceof Error && e.message.startsWith("401");
}

export function listSquishyTypes(includeInactive = false): Promise<SquishyType[]> {
  return request(`/squishy-types${includeInactive ? "?include_inactive=true" : ""}`);
}

export function createSquishyType(body: {
  name: string;
  is_giveaway_item?: boolean;
}): Promise<SquishyType> {
  return request("/squishy-types", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function deactivateSquishyType(id: number): Promise<SquishyType> {
  return request(`/squishy-types/${id}/deactivate`, { method: "POST" });
}

export function reactivateSquishyType(id: number): Promise<SquishyType> {
  return request(`/squishy-types/${id}/reactivate`, { method: "POST" });
}

export function listWallSets(): Promise<WallSet[]> {
  return request("/wall-sets");
}

export function getWallSet(id: number): Promise<WallSet> {
  return request(`/wall-sets/${id}`);
}

export async function downloadSquishyTypeLabelSheet(
  squishyTypeId: number,
  quantity: number,
): Promise<Blob> {
  const resp = await fetch(
    `${API_BASE}/squishy-types/${squishyTypeId}/label-sheet?quantity=${quantity}`,
    { credentials: "include" },
  );
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.blob();
}

export async function uploadOrders(
  wallSetId: number,
  csvFile: File,
  pdfFile: File,
): Promise<UploadSummary> {
  const form = new FormData();
  form.append("csv_file", csvFile);
  form.append("pdf_file", pdfFile);
  return request(`/wall-sets/${wallSetId}/upload`, { method: "POST", body: form });
}

export function uploadOrdersToNewWallSet(
  csvFile: File,
  pdfFile: File,
): Promise<UploadToNewWallSetSummary> {
  const form = new FormData();
  form.append("csv_file", csvFile);
  form.append("pdf_file", pdfFile);
  return request("/wall-sets/upload", { method: "POST", body: form });
}

export function scanBarcode(wallSetId: number, barcode: string): Promise<ScanResponse> {
  return request(`/wall-sets/${wallSetId}/scan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ barcode }),
  });
}

export function listShipments(wallSetId: number): Promise<ShipmentRow[]> {
  return request(`/wall-sets/${wallSetId}/shipments`);
}

export async function downloadShipmentLabel(
  wallSetId: number,
  shipmentId: number,
): Promise<Blob> {
  const resp = await fetch(
    `${API_BASE}/wall-sets/${wallSetId}/shipments/${shipmentId}/label`,
    { credentials: "include" },
  );
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.blob();
}

export function login(password: string): Promise<{ authenticated: true }> {
  return request("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
}

export function logout(): Promise<{ authenticated: false }> {
  return request("/auth/logout", { method: "POST" });
}

export async function checkAuth(): Promise<boolean> {
  const resp = await fetch(`${API_BASE}/auth/me`, { credentials: "include" });
  return resp.ok;
}

export function floorLogin(pin: string): Promise<{ authenticated: true }> {
  return request("/auth/floor-login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pin }),
  });
}

export async function checkFloorAccess(): Promise<boolean> {
  const resp = await fetch(`${API_BASE}/auth/floor-me`, { credentials: "include" });
  return resp.ok;
}

export function listFinancials(): Promise<FinancialSummary[]> {
  return request("/financials");
}

export async function getFinancials(wallSetId: number): Promise<FinancialRecord | null> {
  const resp = await fetch(`${API_BASE}/wall-sets/${wallSetId}/financials`, { credentials: "include" });
  if (resp.status === 404) return null; // no record entered yet -- expected, not an error
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json();
}

export function upsertFinancials(
  wallSetId: number,
  body: FinancialRecordUpsertBody,
): Promise<FinancialRecord> {
  return request(`/wall-sets/${wallSetId}/financials`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function triggerBlobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
