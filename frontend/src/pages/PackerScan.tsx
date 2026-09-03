import { FormEvent, useEffect, useRef, useState } from "react";
import { ScanResponse, WallSet, downloadShipmentLabel, listWallSets, scanBarcode, triggerBlobDownload } from "../api";

export default function PackerScan() {
  const [wallSets, setWallSets] = useState<WallSet[]>([]);
  const uploadedWallSets = wallSets.filter((w) => w.orders_uploaded);
  const [wallSetId, setWallSetId] = useState<number | "">("");
  const [barcode, setBarcode] = useState("");
  const [lastResult, setLastResult] = useState<ScanResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [awaitingPacked, setAwaitingPacked] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listWallSets()
      .then(setWallSets)
      .catch((e) => setError(String(e)));
  }, []);

  // Runs after React commits the DOM (unlike a focus() call made directly
  // in the submit handler, which can fire before the re-render that clears
  // the `disabled` attribute and silently no-op on a still-disabled input).
  useEffect(() => {
    if (!awaitingPacked && !busy) {
      inputRef.current?.focus();
    }
  }, [wallSetId, awaitingPacked, busy]);

  async function handleScanSubmit(e: FormEvent) {
    e.preventDefault();
    // The scan input is disabled while awaitingPacked, which already blocks
    // typing/submission at the browser level -- this guard is defense in
    // depth in case a submit ever fires some other way.
    if (wallSetId === "" || !barcode.trim() || busy || awaitingPacked) return;
    setBusy(true);
    setError(null);
    const scanned = barcode.trim();
    setBarcode("");

    try {
      const result = await scanBarcode(wallSetId, scanned);
      setLastResult(result);

      if (result.status === "complete") {
        const blob = await downloadShipmentLabel(wallSetId, result.shipment_id);
        triggerBlobDownload(blob, `${result.tracking_number}.pdf`);
        setAwaitingPacked(true);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  function handlePacked() {
    setAwaitingPacked(false);
    setLastResult(null);
  }

  function resultLabel(result: ScanResponse): string {
    switch (result.status) {
      case "unknown_barcode":
        return "Unknown barcode -- not in the catalog.";
      case "no_shipment_needs_it":
        return "No open shipment needs this item right now.";
      case "in_progress":
        return result.message;
      case "complete":
        return "Shipment complete! Label downloading...";
      default:
        return "";
    }
  }

  return (
    <div>
      <h1>Packer Scan</h1>
      {error && <p className="error-text">{error}</p>}

      <section className="panel">
        <label htmlFor="wall-set-select">Wall set</label>
        <select
          id="wall-set-select"
          value={wallSetId}
          onChange={(e) => {
            setWallSetId(e.target.value ? Number(e.target.value) : "");
            setLastResult(null);
            setAwaitingPacked(false);
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
          <form onSubmit={handleScanSubmit}>
            <label htmlFor="barcode-input">Scan item</label>
            <input
              id="barcode-input"
              ref={inputRef}
              className="scan-input"
              type="text"
              autoFocus
              autoComplete="off"
              value={barcode}
              disabled={busy || awaitingPacked}
              onChange={(e) => setBarcode(e.target.value)}
            />
          </form>

          {lastResult && (
            <div className={`scan-result status-${lastResult.status}`}>
              {lastResult.status === "in_progress" && (
                <div className="bin-number">Bin {lastResult.bin_number}</div>
              )}
              <div>{resultLabel(lastResult)}</div>
            </div>
          )}

          {awaitingPacked && (
            <button type="button" onClick={handlePacked} style={{ marginTop: "1rem", width: "100%" }}>
              Packed
            </button>
          )}
        </section>
      )}
    </div>
  );
}
