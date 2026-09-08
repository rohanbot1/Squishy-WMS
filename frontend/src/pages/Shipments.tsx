import { Fragment, useEffect, useState } from "react";
import {
  ShipmentRow,
  WallSet,
  downloadShipmentLabel,
  listShipments,
  listWallSets,
  triggerBlobDownload,
} from "../api";
import { useAuthErrorHandler } from "../useAuthErrorHandler";

type StatusFilter = "open" | "complete" | "all";

interface ShipmentsProps {
  onAuthError: () => void;
}

export default function Shipments({ onAuthError }: ShipmentsProps) {
  const handleAuthAwareError = useAuthErrorHandler(onAuthError, "/floor-login");
  const [wallSets, setWallSets] = useState<WallSet[]>([]);
  const uploadedWallSets = wallSets.filter((w) => w.orders_uploaded);
  const [wallSetId, setWallSetId] = useState<number | "">("");

  const [shipments, setShipments] = useState<ShipmentRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [statusFilter, setStatusFilter] = useState<StatusFilter>("open");
  const [search, setSearch] = useState("");
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const [downloadingId, setDownloadingId] = useState<number | null>(null);

  useEffect(() => {
    listWallSets()
      .then(setWallSets)
      .catch((e) => handleAuthAwareError(e, setError));
  }, []);

  useEffect(() => {
    if (wallSetId === "") {
      setShipments([]);
      return;
    }
    setLoading(true);
    setError(null);
    listShipments(wallSetId)
      .then(setShipments)
      .catch((e) => handleAuthAwareError(e, setError))
      .finally(() => setLoading(false));
  }, [wallSetId]);

  function toggleExpanded(id: number) {
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  async function handleDownloadLabel(shipment: ShipmentRow) {
    if (wallSetId === "") return;
    setError(null);
    setDownloadingId(shipment.id);
    try {
      const blob = await downloadShipmentLabel(wallSetId, shipment.id);
      triggerBlobDownload(blob, `${shipment.tracking_number}.pdf`);
    } catch (e) {
      handleAuthAwareError(e, setError);
    } finally {
      setDownloadingId(null);
    }
  }

  const filtered = shipments
    .filter((s) => {
      if (statusFilter === "open" && s.is_complete) return false;
      if (statusFilter === "complete" && !s.is_complete) return false;
      if (search.trim() && !s.tracking_number.toLowerCase().includes(search.trim().toLowerCase())) {
        return false;
      }
      return true;
    })
    .sort((a, b) => {
      if (a.is_complete !== b.is_complete) return a.is_complete ? 1 : -1;
      const aBin = a.bin_number ?? Infinity;
      const bBin = b.bin_number ?? Infinity;
      if (aBin !== bBin) return aBin - bBin;
      return a.tracking_number.localeCompare(b.tracking_number);
    });

  return (
    <div>
      <h1>Shipments</h1>
      {error && <p className="error-text">{error}</p>}

      <section className="panel">
        <label htmlFor="wall-set-select">Wall set</label>
        <select
          id="wall-set-select"
          value={wallSetId}
          onChange={(e) => {
            setWallSetId(e.target.value ? Number(e.target.value) : "");
            setExpandedIds(new Set());
          }}
        >
          <option value="">Select a wall set...</option>
          {uploadedWallSets.map((w) => (
            <option key={w.id} value={w.id}>
              {w.label} (#{w.id}, uploaded)
            </option>
          ))}
        </select>
      </section>

      {wallSetId !== "" && (
        <section className="panel">
          <div className="row">
            <div>
              <label htmlFor="status-filter">Status</label>
              <select
                id="status-filter"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
              >
                <option value="open">Open</option>
                <option value="complete">Complete</option>
                <option value="all">All</option>
              </select>
            </div>
            <div>
              <label htmlFor="tracking-search">Search tracking number</label>
              <input
                id="tracking-search"
                type="text"
                placeholder="e.g. 9200190..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>

          {loading ? (
            <p className="muted" style={{ marginTop: "1rem" }}>
              Loading...
            </p>
          ) : (
            <>
              <p className="muted" style={{ marginTop: "1rem" }}>
                {filtered.length} of {shipments.length} shipment(s) shown.
              </p>

              <table style={{ marginTop: "0.5rem" }}>
                <thead>
                  <tr>
                    <th>Tracking number</th>
                    <th>Status</th>
                    <th>Bin</th>
                    <th>Items</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((shipment) => (
                    <Fragment key={shipment.id}>
                      <tr>
                        <td>{shipment.tracking_number}</td>
                        <td>
                          <span className={`pill ${shipment.is_complete ? "ok" : "warn"}`}>
                            {shipment.is_complete ? "Complete" : "Open"}
                          </span>
                        </td>
                        <td>{shipment.bin_number ?? "--"}</td>
                        <td>{shipment.requirements.length}</td>
                        <td>
                          <div className="row">
                            <button
                              type="button"
                              className="secondary"
                              onClick={() => toggleExpanded(shipment.id)}
                            >
                              {expandedIds.has(shipment.id) ? "Hide" : "Details"}
                            </button>
                            {shipment.is_complete && (
                              <button
                                type="button"
                                className="secondary"
                                disabled={downloadingId === shipment.id}
                                onClick={() => handleDownloadLabel(shipment)}
                              >
                                {downloadingId === shipment.id ? "Downloading..." : "Download label"}
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                      {expandedIds.has(shipment.id) && (
                        <tr>
                          <td colSpan={5}>
                            <table>
                              <thead>
                                <tr>
                                  <th>Squishy type</th>
                                  <th>Required</th>
                                  <th>Scanned</th>
                                </tr>
                              </thead>
                              <tbody>
                                {shipment.requirements.map((r) => (
                                  <tr key={r.squishy_type_id}>
                                    <td>{r.name}</td>
                                    <td>{r.quantity_required}</td>
                                    <td>{r.quantity_scanned}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>

              {filtered.length === 0 && (
                <p className="muted" style={{ marginTop: "0.75rem" }}>
                  No shipments match this filter.
                </p>
              )}
            </>
          )}
        </section>
      )}
    </div>
  );
}
