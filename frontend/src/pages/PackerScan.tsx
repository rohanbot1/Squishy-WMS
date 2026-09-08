import { FormEvent, useEffect, useRef, useState } from "react";
import { ScanResponse, WallSet, downloadShipmentLabel, listWallSets, scanBarcode, triggerBlobDownload } from "../api";
import { useTranslation } from "../i18n";
import { useAuthErrorHandler } from "../useAuthErrorHandler";

interface PackerScanProps {
  onAuthError: () => void;
}

export default function PackerScan({ onAuthError }: PackerScanProps) {
  const { t } = useTranslation();
  const handleAuthAwareError = useAuthErrorHandler(onAuthError, "/floor-login");
  const [wallSets, setWallSets] = useState<WallSet[]>([]);
  const uploadedWallSets = wallSets.filter((w) => w.orders_uploaded);
  const [wallSetId, setWallSetId] = useState<number | "">("");
  const [barcode, setBarcode] = useState("");
  const [lastResult, setLastResult] = useState<ScanResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listWallSets()
      .then(setWallSets)
      .catch((e) => handleAuthAwareError(e, setError));
  }, []);

  // Runs after React commits the DOM (unlike a focus() call made directly
  // in the submit handler, which can fire before the re-render that clears
  // the `disabled` attribute and silently no-op on a still-disabled input).
  useEffect(() => {
    if (!busy) {
      inputRef.current?.focus();
    }
  }, [wallSetId, busy]);

  async function handleScanSubmit(e: FormEvent) {
    e.preventDefault();
    if (wallSetId === "" || !barcode.trim() || busy) return;
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
      }
    } catch (e) {
      handleAuthAwareError(e, setError);
    } finally {
      setBusy(false);
    }
  }

  function resultLabel(result: ScanResponse): string {
    switch (result.status) {
      case "unknown_barcode":
        return t("packerScan.unknownBarcode");
      case "no_shipment_needs_it":
        return t("packerScan.noShipmentNeedsIt");
      case "in_progress": {
        // Squishy type names come through verbatim -- never translated --
        // interpolated into the localized "still needs" template.
        const items = result.remaining
          .map((r) => `${r.name} ×${r.quantity_remaining}`)
          .join(", ");
        return (
          t("packerScan.goesInBin", { n: result.bin_number }) +
          " " +
          t("packerScan.stillNeeds", { items })
        );
      }
      case "complete": {
        // bin_number is only ever set for a shipment that took more than
        // one scan (a bundle) -- a single-item order that completes on
        // its one and only scan never occupies a bin, so it stays null.
        if (result.bin_number != null) {
          const items = result.items.map((i) => `${i.name} ×${i.quantity}`).join(", ");
          return t("packerScan.binComplete", { n: result.bin_number, items });
        }
        return t("packerScan.shipmentComplete");
      }
      default:
        return "";
    }
  }

  return (
    <div>
      <h1>Packer Scan</h1>
      {error && <p className="error-text">{error}</p>}

      <section className="panel">
        <label htmlFor="wall-set-select">{t("packerScan.wallSetLabel")}</label>
        <select
          id="wall-set-select"
          value={wallSetId}
          onChange={(e) => {
            setWallSetId(e.target.value ? Number(e.target.value) : "");
            setLastResult(null);
          }}
        >
          <option value="">{t("packerScan.wallSetPlaceholder")}</option>
          {uploadedWallSets.map((w) => (
            <option key={w.id} value={w.id}>
              {w.label} (#{w.id}, {t("packerScan.uploaded")})
            </option>
          ))}
        </select>
      </section>

      {wallSetId !== "" && (
        <section className="panel">
          <form onSubmit={handleScanSubmit}>
            <label htmlFor="barcode-input">{t("packerScan.scanLabel")}</label>
            <input
              id="barcode-input"
              ref={inputRef}
              className="scan-input"
              type="text"
              autoFocus
              autoComplete="off"
              value={barcode}
              disabled={busy}
              onChange={(e) => setBarcode(e.target.value)}
            />
          </form>

          {lastResult && (
            <div className={`scan-result status-${lastResult.status}`}>
              {lastResult.status === "in_progress" && (
                <div className="bin-number">{t("packerScan.bin", { n: lastResult.bin_number })}</div>
              )}
              <div>{resultLabel(lastResult)}</div>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
