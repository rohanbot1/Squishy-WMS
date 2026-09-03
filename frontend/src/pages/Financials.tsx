import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { FinancialSummary, WallSet, listFinancials, listWallSets } from "../api";

function formatRoi(roi: number | null): string {
  return roi === null ? "Not yet calculated" : `${(roi * 100).toFixed(1)}%`;
}

function formatMoney(n: number): string {
  return `$${n.toFixed(2)}`;
}

export default function Financials() {
  const [wallSets, setWallSets] = useState<WallSet[]>([]);
  const [records, setRecords] = useState<FinancialSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    Promise.all([listWallSets(), listFinancials()])
      .then(([ws, fin]) => {
        setWallSets(ws);
        setRecords(fin);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p className="muted">Loading...</p>;

  const recordedWallSetIds = new Set(records.map((r) => r.wall_set_id));
  const unrecorded = wallSets.filter((ws) => !recordedWallSetIds.has(ws.id));

  return (
    <div>
      <h1>Financials</h1>
      {error && <p className="error-text">{error}</p>}

      <h2>Recorded streams</h2>
      {records.length === 0 ? (
        <p className="muted">No financial records yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Wall set</th>
              <th>Streamer</th>
              <th>Stream start</th>
              <th>Revenue</th>
              <th>Profit</th>
              <th>ROI</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {records.map((r) => (
              <tr key={r.wall_set_id}>
                <td>{r.wall_set_label}</td>
                <td>{r.streamer}</td>
                <td>{new Date(r.stream_started_at).toLocaleString()}</td>
                <td>{formatMoney(r.revenue)}</td>
                <td>{formatMoney(r.profit)}</td>
                <td>{formatRoi(r.roi)}</td>
                <td>
                  <Link to={`/financials/${r.wall_set_id}`}>Edit</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2 style={{ marginTop: "2rem" }}>Wall sets without a financial record</h2>
      {unrecorded.length === 0 ? (
        <p className="muted">Every wall set has financials recorded.</p>
      ) : (
        <ul>
          {unrecorded.map((ws) => (
            <li key={ws.id}>
              {ws.label} -- <Link to={`/financials/${ws.id}`}>Add financials</Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
