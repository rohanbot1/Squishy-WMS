import { FormEvent, useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import {
  SquishyType,
  WallSet,
  getFinancials,
  getWallSet,
  isUnauthorizedError,
  listSquishyTypes,
  upsertFinancials,
} from "../api";

function toDatetimeLocal(iso: string): string {
  return iso.slice(0, 16); // "YYYY-MM-DDTHH:mm:ss..." -> "YYYY-MM-DDTHH:mm"
}

function formatRoi(roi: number | null): string {
  return roi === null ? "—" : `${(roi * 100).toFixed(1)}%`;
}

function formatMoney(n: number): string {
  return `$${n.toFixed(2)}`;
}

interface FinancialDetailProps {
  onAuthError: () => void;
}

export default function FinancialDetail({ onAuthError }: FinancialDetailProps) {
  const { wallSetId } = useParams<{ wallSetId: string }>();
  const id = Number(wallSetId);
  const navigate = useNavigate();

  const [wallSet, setWallSet] = useState<WallSet | null>(null);
  const [squishyTypes, setSquishyTypes] = useState<SquishyType[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const [streamer, setStreamer] = useState("");
  const [streamStartedAt, setStreamStartedAt] = useState("");
  const [streamEndedAt, setStreamEndedAt] = useState("");
  const [revenue, setRevenue] = useState("");
  const [fees, setFees] = useState("");
  const [bidAverage, setBidAverage] = useState("");
  const [giveawayTypeId, setGiveawayTypeId] = useState<number | "">("");
  const [giveawayQuantity, setGiveawayQuantity] = useState("");
  const [notes, setNotes] = useState("");
  const [itemCosts, setItemCosts] = useState<Record<number, string>>({});

  useEffect(() => {
    setLoading(true);
    setError(null);
    Promise.all([getWallSet(id), listSquishyTypes(), getFinancials(id)])
      .then(([ws, types, record]) => {
        setWallSet(ws);
        setSquishyTypes(types);
        if (record) {
          setStreamer(record.streamer);
          setStreamStartedAt(toDatetimeLocal(record.stream_started_at));
          setStreamEndedAt(toDatetimeLocal(record.stream_ended_at));
          setRevenue(String(record.revenue));
          setFees(String(record.fees));
          setBidAverage(String(record.bid_average));
          setGiveawayTypeId(record.giveaway?.squishy_type_id ?? "");
          setGiveawayQuantity(record.giveaway?.quantity != null ? String(record.giveaway.quantity) : "");
          setNotes(record.notes ?? "");
          const costs: Record<number, string> = {};
          for (const item of record.items) {
            if (item.unit_cost != null) costs[item.squishy_type_id] = String(item.unit_cost);
          }
          setItemCosts(costs);
        }
      })
      .catch((e) => {
        if (isUnauthorizedError(e)) {
          onAuthError();
          navigate("/login");
          return;
        }
        setError(String(e));
      })
      .finally(() => setLoading(false));
  }, [id, navigate, onAuthError]);

  const giveawayTypes = squishyTypes.filter((t) => t.is_giveaway_item);

  const revenueNum = parseFloat(revenue) || 0;
  const feesNum = parseFloat(fees) || 0;
  const totalItemCost = (wallSet?.items ?? []).reduce((sum, item) => {
    const raw = itemCosts[item.squishy_type_id];
    const cost = raw ? parseFloat(raw) : NaN;
    return sum + (Number.isFinite(cost) ? cost * item.quantity : 0);
  }, 0);
  const profit = revenueNum - feesNum - totalItemCost;
  const roi = totalItemCost > 0 ? profit / totalItemCost : null;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    setSaving(true);
    try {
      await upsertFinancials(id, {
        streamer,
        stream_started_at: streamStartedAt,
        stream_ended_at: streamEndedAt,
        revenue: revenueNum,
        fees: feesNum,
        bid_average: parseFloat(bidAverage) || 0,
        giveaway_squishy_type_id: giveawayTypeId === "" ? null : giveawayTypeId,
        giveaway_quantity: giveawayQuantity === "" ? null : parseInt(giveawayQuantity, 10),
        notes: notes || null,
        item_costs: Object.entries(itemCosts)
          .filter(([, v]) => v.trim() !== "")
          .map(([squishyTypeId, v]) => ({ squishy_type_id: Number(squishyTypeId), unit_cost: parseFloat(v) })),
      });
      setSaved(true);
    } catch (e) {
      if (isUnauthorizedError(e)) {
        onAuthError();
        navigate("/login");
        return;
      }
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <p className="muted">Loading...</p>;

  return (
    <div>
      <h1>Financials -- {wallSet?.label}</h1>
      <p className="muted">
        <Link to="/financials">&larr; All financial records</Link>
      </p>
      {error && <p className="error-text">{error}</p>}
      {saved && <p className="pill ok">Saved.</p>}

      <section className="panel">
        <form onSubmit={handleSubmit}>
          <div className="row">
            <div>
              <label htmlFor="streamer">Streamer</label>
              <input id="streamer" type="text" value={streamer} onChange={(e) => setStreamer(e.target.value)} />
            </div>
            <div>
              <label htmlFor="stream-start">Stream start</label>
              <input
                id="stream-start"
                type="datetime-local"
                value={streamStartedAt}
                onChange={(e) => setStreamStartedAt(e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="stream-end">Stream end</label>
              <input
                id="stream-end"
                type="datetime-local"
                value={streamEndedAt}
                onChange={(e) => setStreamEndedAt(e.target.value)}
              />
            </div>
          </div>

          <div className="row" style={{ marginTop: "0.75rem" }}>
            <div>
              <label htmlFor="revenue">Revenue ($)</label>
              <input id="revenue" type="number" step="0.01" value={revenue} onChange={(e) => setRevenue(e.target.value)} />
            </div>
            <div>
              <label htmlFor="fees">Fees ($)</label>
              <input id="fees" type="number" step="0.01" value={fees} onChange={(e) => setFees(e.target.value)} />
            </div>
            <div>
              <label htmlFor="bid-average">Average bid ($)</label>
              <input
                id="bid-average"
                type="number"
                step="0.01"
                value={bidAverage}
                onChange={(e) => setBidAverage(e.target.value)}
              />
            </div>
          </div>

          <div className="row" style={{ marginTop: "0.75rem" }}>
            <div>
              <label htmlFor="giveaway-type">Giveaway item</label>
              <select
                id="giveaway-type"
                value={giveawayTypeId}
                onChange={(e) => setGiveawayTypeId(e.target.value ? Number(e.target.value) : "")}
              >
                <option value="">None</option>
                {giveawayTypes.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="giveaway-qty">Giveaway quantity</label>
              <input
                id="giveaway-qty"
                type="number"
                min={0}
                value={giveawayQuantity}
                onChange={(e) => setGiveawayQuantity(e.target.value)}
                disabled={giveawayTypeId === ""}
              />
            </div>
          </div>

          <div style={{ marginTop: "0.75rem" }}>
            <label htmlFor="notes">Notes</label>
            <textarea
              id="notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              style={{ width: "100%", minHeight: "4rem" }}
            />
          </div>

          <h2>Item costs</h2>
          <p className="muted">
            Quantities come from this wall set's manifest. Leave a cost blank if it isn't known yet --
            ROI shows as "—" until at least one item has a cost entered.
          </p>
          <table>
            <thead>
              <tr>
                <th>Squishy type</th>
                <th>Quantity</th>
                <th>Unit cost ($)</th>
                <th>Line cost</th>
              </tr>
            </thead>
            <tbody>
              {(wallSet?.items ?? []).map((item) => {
                const raw = itemCosts[item.squishy_type_id] ?? "";
                const cost = raw ? parseFloat(raw) : NaN;
                const lineCost = Number.isFinite(cost) ? cost * item.quantity : null;
                return (
                  <tr key={item.squishy_type_id}>
                    <td>{item.name}</td>
                    <td>{item.quantity}</td>
                    <td>
                      <input
                        type="number"
                        step="0.01"
                        min={0}
                        style={{ width: "6rem" }}
                        value={raw}
                        onChange={(e) =>
                          setItemCosts((c) => ({ ...c, [item.squishy_type_id]: e.target.value }))
                        }
                      />
                    </td>
                    <td>{lineCost === null ? "—" : formatMoney(lineCost)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <div className="panel" style={{ marginTop: "1rem", background: "var(--bg)" }}>
            <div className="row">
              <span>
                <strong>Total item cost:</strong> {formatMoney(totalItemCost)}
              </span>
              <span>
                <strong>Profit:</strong> {formatMoney(profit)}
              </span>
              <span>
                <strong>ROI:</strong> {formatRoi(roi)}
              </span>
            </div>
          </div>

          <button type="submit" disabled={saving} style={{ marginTop: "1rem" }}>
            {saving ? "Saving..." : "Save"}
          </button>
        </form>
      </section>
    </div>
  );
}
