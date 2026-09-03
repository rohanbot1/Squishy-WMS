import { FormEvent, useEffect, useState } from "react";
import {
  SquishyType,
  UploadSummary,
  WallSet,
  createSquishyType,
  createWallSet,
  downloadLabelSheet,
  downloadSquishyTypeLabelSheet,
  listSquishyTypes,
  triggerBlobDownload,
  uploadOrders,
} from "../api";

interface DraftItem {
  squishy_type_id: number;
  quantity: number;
}

export default function WallBuilder() {
  const [squishyTypes, setSquishyTypes] = useState<SquishyType[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [newTypeName, setNewTypeName] = useState("");

  const [printQuantities, setPrintQuantities] = useState<Record<number, string>>({});
  const [printingTypeId, setPrintingTypeId] = useState<number | null>(null);

  const [wallLabel, setWallLabel] = useState("");
  const [selectedTypeId, setSelectedTypeId] = useState<number | "">("");
  const [selectedQty, setSelectedQty] = useState(1);
  const [draftItems, setDraftItems] = useState<DraftItem[]>([]);

  const [wallSet, setWallSet] = useState<WallSet | null>(null);
  const [creatingWallSet, setCreatingWallSet] = useState(false);

  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadSummary, setUploadSummary] = useState<UploadSummary | null>(null);

  useEffect(() => {
    refreshSquishyTypes();
  }, []);

  function refreshSquishyTypes() {
    listSquishyTypes()
      .then(setSquishyTypes)
      .catch((e) => setError(String(e)));
  }

  async function handleCreateSquishyType(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!newTypeName.trim()) return;
    try {
      await createSquishyType({ name: newTypeName.trim() });
      setNewTypeName("");
      refreshSquishyTypes();
    } catch (e) {
      setError(String(e));
    }
  }

  async function handlePrintBarcode(squishyType: SquishyType) {
    setError(null);
    const raw = printQuantities[squishyType.id] ?? "1";
    const quantity = Math.max(1, parseInt(raw, 10) || 1);
    setPrintingTypeId(squishyType.id);
    try {
      const blob = await downloadSquishyTypeLabelSheet(squishyType.id, quantity);
      triggerBlobDownload(blob, `${squishyType.internal_code}.pdf`);
    } catch (e) {
      setError(String(e));
    } finally {
      setPrintingTypeId(null);
    }
  }

  function addDraftItem() {
    if (selectedTypeId === "" || selectedQty <= 0) return;
    setDraftItems((items) => {
      const existing = items.find((i) => i.squishy_type_id === selectedTypeId);
      if (existing) {
        return items.map((i) =>
          i.squishy_type_id === selectedTypeId ? { ...i, quantity: i.quantity + selectedQty } : i,
        );
      }
      return [...items, { squishy_type_id: selectedTypeId as number, quantity: selectedQty }];
    });
    setSelectedQty(1);
  }

  function removeDraftItem(id: number) {
    setDraftItems((items) => items.filter((i) => i.squishy_type_id !== id));
  }

  async function handleCreateWallSet(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!wallLabel.trim() || draftItems.length === 0) return;
    setCreatingWallSet(true);
    try {
      const created = await createWallSet({ label: wallLabel.trim(), items: draftItems });
      setWallSet(created);
      setUploadSummary(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setCreatingWallSet(false);
    }
  }

  async function handleDownloadLabelSheet() {
    if (!wallSet) return;
    setError(null);
    try {
      const blob = await downloadLabelSheet(wallSet.id);
      triggerBlobDownload(blob, `${wallSet.label}-label-sheet.pdf`);
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleUpload(e: FormEvent) {
    e.preventDefault();
    if (!wallSet || !csvFile || !pdfFile) return;
    setError(null);
    setUploading(true);
    try {
      const summary = await uploadOrders(wallSet.id, csvFile, pdfFile);
      setUploadSummary(summary);
    } catch (e) {
      setError(String(e));
    } finally {
      setUploading(false);
    }
  }

  const nameById = new Map(squishyTypes.map((t) => [t.id, t.name]));

  return (
    <div>
      <h1>Wall Builder</h1>
      {error && <p className="error-text">{error}</p>}

      <section className="panel">
        <h2>Squishy type catalog</h2>
        <form className="row" onSubmit={handleCreateSquishyType}>
          <div>
            <label htmlFor="type-name">Display name</label>
            <input
              id="type-name"
              type="text"
              placeholder="e.g. Yellow Butter"
              value={newTypeName}
              onChange={(e) => setNewTypeName(e.target.value)}
            />
          </div>
          <button
            type="submit"
            style={{ marginTop: "1.2rem" }}
            disabled={!newTypeName.trim()}
          >
            Add type
          </button>
        </form>
        <p className="muted">{squishyTypes.length} squishy type(s) in the catalog.</p>

        {squishyTypes.length > 0 && (
          <table style={{ marginTop: "0.75rem" }}>
            <thead>
              <tr>
                <th>Name</th>
                <th>Quantity</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {squishyTypes.map((t) => (
                <tr key={t.id}>
                  <td>{t.name}</td>
                  <td>
                    <input
                      type="number"
                      min={1}
                      value={printQuantities[t.id] ?? "1"}
                      onChange={(e) =>
                        setPrintQuantities((q) => ({ ...q, [t.id]: e.target.value }))
                      }
                      style={{ width: "4rem" }}
                    />
                  </td>
                  <td>
                    <button
                      type="button"
                      className="secondary"
                      disabled={printingTypeId === t.id}
                      onClick={() => handlePrintBarcode(t)}
                    >
                      {printingTypeId === t.id ? "Printing..." : "Print barcode"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel">
        <h2>Build a wall</h2>
        <div className="row">
          <div>
            <label htmlFor="wall-label">Wall label</label>
            <input
              id="wall-label"
              type="text"
              placeholder="e.g. 9/1 stream"
              value={wallLabel}
              onChange={(e) => setWallLabel(e.target.value)}
            />
          </div>
        </div>

        <div className="row" style={{ marginTop: "0.75rem" }}>
          <div>
            <label htmlFor="item-type">Squishy type</label>
            <select
              id="item-type"
              value={selectedTypeId}
              onChange={(e) => setSelectedTypeId(e.target.value ? Number(e.target.value) : "")}
            >
              <option value="">Select...</option>
              {squishyTypes.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="item-qty">Quantity</label>
            <input
              id="item-qty"
              type="number"
              min={1}
              value={selectedQty}
              onChange={(e) => setSelectedQty(Number(e.target.value))}
            />
          </div>
          <button type="button" className="secondary" style={{ marginTop: "1.2rem" }} onClick={addDraftItem}>
            Add to wall
          </button>
        </div>

        {draftItems.length > 0 && (
          <table style={{ marginTop: "1rem" }}>
            <thead>
              <tr>
                <th>Type</th>
                <th>Quantity</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {draftItems.map((item) => (
                <tr key={item.squishy_type_id}>
                  <td>{nameById.get(item.squishy_type_id)}</td>
                  <td>{item.quantity}</td>
                  <td>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => removeDraftItem(item.squishy_type_id)}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div style={{ marginTop: "1rem" }}>
          <button
            type="button"
            onClick={handleCreateWallSet}
            disabled={creatingWallSet || !wallLabel.trim() || draftItems.length === 0}
          >
            {creatingWallSet ? "Creating..." : "Create wall set"}
          </button>
        </div>

        {wallSet && (
          <p className="muted" style={{ marginTop: "0.75rem" }}>
            Created wall set #{wallSet.id} ({wallSet.label}).
          </p>
        )}
      </section>

      {wallSet && (
        <section className="panel">
          <h2>Barcode labels</h2>
          <p className="muted">Print one barcode per physical unit for this wall's manifest.</p>
          <button type="button" onClick={handleDownloadLabelSheet}>
            Download label sheet PDF
          </button>
        </section>
      )}

      {wallSet && (
        <section className="panel">
          <h2>Upload orders</h2>
          <form onSubmit={handleUpload}>
            <div className="row">
              <div>
                <label htmlFor="csv-file">TikTok CSV export</label>
                <input
                  id="csv-file"
                  type="file"
                  accept=".csv"
                  onChange={(e) => setCsvFile(e.target.files?.[0] ?? null)}
                />
              </div>
              <div>
                <label htmlFor="pdf-file">Shipping label / packing slip PDF</label>
                <input
                  id="pdf-file"
                  type="file"
                  accept=".pdf"
                  onChange={(e) => setPdfFile(e.target.files?.[0] ?? null)}
                />
              </div>
            </div>
            <button type="submit" disabled={uploading || !csvFile || !pdfFile} style={{ marginTop: "1rem" }}>
              {uploading ? "Uploading..." : "Upload and ingest"}
            </button>
          </form>

          {uploadSummary && (
            <div style={{ marginTop: "1rem" }}>
              <p>
                <span className="pill ok">{uploadSummary.shipments_created} shipments</span>{" "}
                <span className="pill ok">{uploadSummary.requirements_created} requirement lines</span>{" "}
                <span className="pill ok">{uploadSummary.labels_matched} label pages matched</span>
              </p>
              {uploadSummary.unmatched_products.length > 0 ? (
                <div>
                  <p className="pill error">
                    {uploadSummary.unmatched_products.length} product name(s) not in the catalog
                  </p>
                  <ul>
                    {uploadSummary.unmatched_products.map((name) => (
                      <li key={name} className="error-text">
                        {name}
                      </li>
                    ))}
                  </ul>
                  <p className="muted">
                    Add these as squishy types above, then re-upload before packing starts.
                  </p>
                </div>
              ) : (
                <p className="pill ok">Every product name matched the catalog.</p>
              )}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
