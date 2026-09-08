import { FormEvent, useEffect, useState } from "react";
import {
  SquishyType,
  UploadToNewWallSetSummary,
  createSquishyType,
  downloadSquishyTypeLabelSheet,
  listSquishyTypes,
  triggerBlobDownload,
  uploadOrdersToNewWallSet,
} from "../api";
import { useTranslation } from "../i18n";
import { useAuthErrorHandler } from "../useAuthErrorHandler";

interface WallBuilderProps {
  onAuthError: () => void;
}

export default function WallBuilder({ onAuthError }: WallBuilderProps) {
  const { t } = useTranslation();
  const handleAuthAwareError = useAuthErrorHandler(onAuthError, "/floor-login");
  const [squishyTypes, setSquishyTypes] = useState<SquishyType[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [newTypeName, setNewTypeName] = useState("");

  const [printQuantities, setPrintQuantities] = useState<Record<number, string>>({});
  const [printingTypeId, setPrintingTypeId] = useState<number | null>(null);

  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadSummary, setUploadSummary] = useState<UploadToNewWallSetSummary | null>(null);

  useEffect(() => {
    refreshSquishyTypes();
  }, []);

  function refreshSquishyTypes() {
    listSquishyTypes()
      .then(setSquishyTypes)
      .catch((e) => handleAuthAwareError(e, setError));
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
      handleAuthAwareError(e, setError);
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
      handleAuthAwareError(e, setError);
    } finally {
      setPrintingTypeId(null);
    }
  }

  async function handleUpload(e: FormEvent) {
    e.preventDefault();
    if (!csvFile || !pdfFile) return;
    setError(null);
    setUploading(true);
    try {
      const summary = await uploadOrdersToNewWallSet(csvFile, pdfFile);
      setUploadSummary(summary);
      setCsvFile(null);
      setPdfFile(null);
    } catch (e) {
      handleAuthAwareError(e, setError);
    } finally {
      setUploading(false);
    }
  }

  return (
    <div>
      <h1>Wall Builder</h1>
      {error && <p className="error-text">{error}</p>}

      <section className="panel">
        <h2>{t("wallBuilder.catalogHeading")}</h2>
        <form className="row" onSubmit={handleCreateSquishyType}>
          <div>
            <label htmlFor="type-name">{t("wallBuilder.displayNameLabel")}</label>
            <input
              id="type-name"
              type="text"
              placeholder={t("wallBuilder.displayNamePlaceholder")}
              value={newTypeName}
              onChange={(e) => setNewTypeName(e.target.value)}
            />
          </div>
          <button
            type="submit"
            style={{ marginTop: "1.2rem" }}
            disabled={!newTypeName.trim()}
          >
            {t("wallBuilder.addType")}
          </button>
        </form>
        <p className="muted">{t("wallBuilder.catalogCount", { count: squishyTypes.length })}</p>

        {squishyTypes.length > 0 && (
          <table style={{ marginTop: "0.75rem" }}>
            <thead>
              <tr>
                <th>{t("wallBuilder.tableName")}</th>
                <th>{t("wallBuilder.tableQuantity")}</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {squishyTypes.map((t2) => (
                <tr key={t2.id}>
                  <td>{t2.name}</td>
                  <td>
                    <input
                      type="number"
                      min={1}
                      value={printQuantities[t2.id] ?? "1"}
                      onChange={(e) =>
                        setPrintQuantities((q) => ({ ...q, [t2.id]: e.target.value }))
                      }
                      style={{ width: "4rem" }}
                    />
                  </td>
                  <td>
                    <button
                      type="button"
                      className="secondary"
                      disabled={printingTypeId === t2.id}
                      onClick={() => handlePrintBarcode(t2)}
                    >
                      {printingTypeId === t2.id ? t("wallBuilder.printing") : t("wallBuilder.printBarcode")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel">
        <h2>{t("wallBuilder.uploadHeading")}</h2>
        <form onSubmit={handleUpload}>
          <div className="row">
            <div>
              <label htmlFor="csv-file">{t("wallBuilder.csvLabel")}</label>
              <input
                id="csv-file"
                type="file"
                accept=".csv"
                onChange={(e) => setCsvFile(e.target.files?.[0] ?? null)}
              />
            </div>
            <div>
              <label htmlFor="pdf-file">{t("wallBuilder.pdfLabel")}</label>
              <input
                id="pdf-file"
                type="file"
                accept=".pdf"
                onChange={(e) => setPdfFile(e.target.files?.[0] ?? null)}
              />
            </div>
          </div>
          <button type="submit" disabled={uploading || !csvFile || !pdfFile} style={{ marginTop: "1rem" }}>
            {uploading ? t("wallBuilder.uploading") : t("wallBuilder.uploadButton")}
          </button>
        </form>

        {uploadSummary && (
          <div style={{ marginTop: "1rem" }}>
            <p className="muted">
              {t("wallBuilder.createdWallSet", {
                id: uploadSummary.wall_set_id,
                label: uploadSummary.wall_set_label,
              })}
            </p>
            <p>
              <span className="pill ok">
                {t("wallBuilder.shipmentsCreated", { count: uploadSummary.shipments_created })}
              </span>{" "}
              <span className="pill ok">
                {t("wallBuilder.requirementsCreated", { count: uploadSummary.requirements_created })}
              </span>{" "}
              <span className="pill ok">
                {t("wallBuilder.labelsMatched", { count: uploadSummary.labels_matched })}
              </span>
            </p>
            {uploadSummary.unmatched_products.length > 0 ? (
              <div>
                <p className="pill error">
                  {t("wallBuilder.unmatchedCount", { count: uploadSummary.unmatched_products.length })}
                </p>
                <ul>
                  {uploadSummary.unmatched_products.map((name) => (
                    <li key={name} className="error-text">
                      {name}
                    </li>
                  ))}
                </ul>
                <p className="muted">{t("wallBuilder.addUnmatchedHint")}</p>
              </div>
            ) : (
              <p className="pill ok">{t("wallBuilder.allMatched")}</p>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
