import { FormEvent, useEffect, useState } from "react";
import {
  ApiError,
  InactiveDuplicateDetail,
  SquishyType,
  UploadToNewWallSetSummary,
  createSquishyType,
  deactivateSquishyType,
  downloadSquishyTypeLabelSheet,
  listSquishyTypes,
  reactivateSquishyType,
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
  const [duplicatePrompt, setDuplicatePrompt] = useState<InactiveDuplicateDetail | null>(null);

  const [printQuantities, setPrintQuantities] = useState<Record<number, string>>({});
  const [printingTypeId, setPrintingTypeId] = useState<number | null>(null);
  const [deactivatingId, setDeactivatingId] = useState<number | null>(null);
  const [reactivatingId, setReactivatingId] = useState<number | null>(null);

  // Deactivated types are deliberately not part of the default fetch --
  // only loaded on demand when this is toggled on, so the catalog's main
  // view stays lean as more weekly types get retired instead of growing
  // unbounded (see app/api.py's include_inactive param).
  const [showDeactivated, setShowDeactivated] = useState(false);
  const [deactivatedTypes, setDeactivatedTypes] = useState<SquishyType[]>([]);

  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadSummary, setUploadSummary] = useState<UploadToNewWallSetSummary | null>(null);

  useEffect(() => {
    refreshSquishyTypes();
  }, []);

  useEffect(() => {
    if (showDeactivated) refreshDeactivatedTypes();
  }, [showDeactivated]);

  function refreshSquishyTypes() {
    listSquishyTypes()
      .then(setSquishyTypes)
      .catch((e) => handleAuthAwareError(e, setError));
  }

  function refreshDeactivatedTypes() {
    listSquishyTypes(true)
      .then((types) => setDeactivatedTypes(types.filter((t) => !t.active)))
      .catch((e) => handleAuthAwareError(e, setError));
  }

  async function handleCreateSquishyType(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setDuplicatePrompt(null);
    if (!newTypeName.trim()) return;
    try {
      await createSquishyType({ name: newTypeName.trim() });
      setNewTypeName("");
      refreshSquishyTypes();
    } catch (e) {
      if (
        e instanceof ApiError &&
        e.status === 409 &&
        typeof e.detail === "object" &&
        e.detail !== null &&
        (e.detail as InactiveDuplicateDetail).reason === "inactive_duplicate"
      ) {
        setDuplicatePrompt(e.detail as InactiveDuplicateDetail);
      } else {
        handleAuthAwareError(e, setError);
      }
    }
  }

  async function handleDeactivate(squishyType: SquishyType) {
    setError(null);
    setDeactivatingId(squishyType.id);
    try {
      await deactivateSquishyType(squishyType.id);
      refreshSquishyTypes();
      if (showDeactivated) refreshDeactivatedTypes();
    } catch (e) {
      handleAuthAwareError(e, setError);
    } finally {
      setDeactivatingId(null);
    }
  }

  async function handleReactivate(squishyTypeId: number) {
    setError(null);
    setReactivatingId(squishyTypeId);
    try {
      await reactivateSquishyType(squishyTypeId);
      refreshSquishyTypes();
      refreshDeactivatedTypes();
      setDuplicatePrompt((current) => (current?.squishy_type_id === squishyTypeId ? null : current));
      if (duplicatePrompt?.squishy_type_id === squishyTypeId) {
        setNewTypeName("");
      }
    } catch (e) {
      handleAuthAwareError(e, setError);
    } finally {
      setReactivatingId(null);
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

      <div className="wb-grid">
        <section className="panel">
          <div className="region-head">
            <h2>{t("wallBuilder.catalogHeading")}</h2>
            <p className="muted">{t("wallBuilder.catalogCount", { count: squishyTypes.length })}</p>
          </div>
          <form className="add-type-form" onSubmit={handleCreateSquishyType}>
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
              disabled={!newTypeName.trim()}
            >
              {t("wallBuilder.addType")}
            </button>
          </form>

          {duplicatePrompt && (
            <div className="inline-alert">
              {duplicatePrompt.message}{" "}
              <button
                type="button"
                className="secondary"
                disabled={reactivatingId === duplicatePrompt.squishy_type_id}
                onClick={() => handleReactivate(duplicatePrompt.squishy_type_id)}
              >
                {reactivatingId === duplicatePrompt.squishy_type_id
                  ? t("wallBuilder.reactivating")
                  : t("wallBuilder.reactivateThisType")}
              </button>
            </div>
          )}

          {squishyTypes.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>{t("wallBuilder.tableName")}</th>
                  <th>{t("wallBuilder.tableQuantity")}</th>
                  <th></th>
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
                        style={{ width: "4.5rem" }}
                      />
                    </td>
                    <td className="actions">
                      <button
                        type="button"
                        className="quiet"
                        disabled={printingTypeId === t2.id}
                        onClick={() => handlePrintBarcode(t2)}
                      >
                        {printingTypeId === t2.id ? t("wallBuilder.printing") : t("wallBuilder.printBarcode")}
                      </button>
                    </td>
                    <td className="actions">
                      <button
                        type="button"
                        className="quiet danger"
                        disabled={deactivatingId === t2.id}
                        onClick={() => handleDeactivate(t2)}
                      >
                        {deactivatingId === t2.id ? t("wallBuilder.deactivating") : t("wallBuilder.deactivate")}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <button
            type="button"
            className="secondary catalog-foot"
            onClick={() => setShowDeactivated((s) => !s)}
          >
            {showDeactivated ? t("wallBuilder.showActive") : t("wallBuilder.showDeactivated")}
          </button>

          {showDeactivated && (
            <div className="deactivated-block">
              {deactivatedTypes.length === 0 ? (
                <p className="muted">{t("wallBuilder.noDeactivatedTypes")}</p>
              ) : (
                <table className="table-dimmed">
                  <thead>
                    <tr>
                      <th>{t("wallBuilder.tableName")}</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {deactivatedTypes.map((t2) => (
                      <tr key={t2.id}>
                        <td>{t2.name}</td>
                        <td className="actions">
                          <button
                            type="button"
                            className="quiet"
                            disabled={reactivatingId === t2.id}
                            onClick={() => handleReactivate(t2.id)}
                          >
                            {reactivatingId === t2.id ? t("wallBuilder.reactivating") : t("wallBuilder.reactivate")}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </section>

        <section className="panel wb-upload">
          <h2>{t("wallBuilder.uploadHeading")}</h2>
          <form onSubmit={handleUpload}>
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
            <button type="submit" disabled={uploading || !csvFile || !pdfFile}>
              {uploading ? t("wallBuilder.uploading") : t("wallBuilder.uploadButton")}
            </button>
          </form>

          {uploadSummary && (
            <div className="upload-result">
              <p className="muted">
                {t("wallBuilder.createdWallSet", {
                  id: uploadSummary.wall_set_id,
                  label: uploadSummary.wall_set_label,
                })}
              </p>
              <p className="stat-strip">
                <span>
                  {t("wallBuilder.shipmentsCreated", { count: uploadSummary.shipments_created })}
                </span>{" "}
                <span>
                  {t("wallBuilder.requirementsCreated", { count: uploadSummary.requirements_created })}
                </span>{" "}
                <span>
                  {t("wallBuilder.labelsMatched", { count: uploadSummary.labels_matched })}
                </span>
              </p>
              {uploadSummary.unmatched_products.length > 0 ? (
                <div>
                  <p className="status-line fault">
                    {t("wallBuilder.unmatchedCount", { count: uploadSummary.unmatched_products.length })}
                  </p>
                  <ul className="fault-list">
                    {uploadSummary.unmatched_products.map((name) => (
                      <li key={name} className="error-text">
                        {name}
                      </li>
                    ))}
                  </ul>
                  <p className="muted">{t("wallBuilder.addUnmatchedHint")}</p>
                </div>
              ) : (
                <p className="status-line">{t("wallBuilder.allMatched")}</p>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
