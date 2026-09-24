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
  renameWallSet,
  triggerBlobDownload,
  uploadOrdersToNewWallSet,
} from "../api";
import { useTranslation } from "../i18n";
import { useAuthErrorHandler } from "../useAuthErrorHandler";

interface WallBuilderProps {
  onAuthError: () => void;
}

// A file field that accepts either a click-to-pick or a drag-and-drop of
// the one right file type. Extension check (case-insensitive) matches the
// `accept` attribute the picker already enforces, and also guards the drop
// path and a "show all files" pick. Wrong type shows `rejectMessage` and
// leaves any previously accepted file untouched.
interface FileDropzoneProps {
  id: string;
  label: string;
  ext: string;
  accept: string;
  file: File | null;
  onFile: (file: File) => void;
  rejectMessage: string;
}

function FileDropzone({ id, label, ext, accept, file, onFile, rejectMessage }: FileDropzoneProps) {
  const { t } = useTranslation();
  const [dragOver, setDragOver] = useState(false);
  const [rejected, setRejected] = useState<string | null>(null);

  function handleFile(f: File | undefined | null) {
    if (!f) return;
    if (f.name.toLowerCase().endsWith(ext)) {
      setRejected(null);
      onFile(f);
    } else {
      setRejected(rejectMessage);
    }
  }

  return (
    <div>
      <label htmlFor={id}>{label}</label>
      <div
        className={`dropzone${dragOver ? " drag-over" : ""}${file ? " has-file" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragEnter={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          handleFile(e.dataTransfer.files?.[0]);
        }}
      >
        <span className="dropzone-hint">{t("wallBuilder.dropHint", { ext })}</span>
        <input id={id} type="file" accept={accept} onChange={(e) => handleFile(e.target.files?.[0])} />
        {/* A dropped file can't be reflected in the native input's own
            "no file chosen" text, so this is the source of truth for what
            will actually be uploaded. */}
        {file && <span className="dropzone-file mono">{file.name}</span>}
      </div>
      {rejected && <p className="error-text dropzone-reject">{rejected}</p>}
    </div>
  );
}

export default function WallBuilder({ onAuthError }: WallBuilderProps) {
  const { t } = useTranslation();
  const handleAuthAwareError = useAuthErrorHandler(onAuthError, "/floor-login");
  const [squishyTypes, setSquishyTypes] = useState<SquishyType[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [newTypeName, setNewTypeName] = useState("");
  const [duplicatePrompt, setDuplicatePrompt] = useState<InactiveDuplicateDetail | null>(null);

  // Live, client-side catalog filter -- same query filters both the active
  // table and the deactivated view. The catalog is small enough that no
  // backend search route is needed.
  const [catalogSearch, setCatalogSearch] = useState("");

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

  // The just-uploaded wall set's current name, editable inline. Kept
  // separate from uploadSummary so a rename doesn't require re-fetching or
  // mutating the summary.
  const [wallSetLabel, setWallSetLabel] = useState("");
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [savingName, setSavingName] = useState(false);

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
      setWallSetLabel(summary.wall_set_label);
      setEditingName(false);
      setCsvFile(null);
      setPdfFile(null);
    } catch (e) {
      handleAuthAwareError(e, setError);
    } finally {
      setUploading(false);
    }
  }

  async function handleSaveName() {
    if (!uploadSummary) return;
    const label = nameDraft.trim();
    if (!label) return;
    setError(null);
    setSavingName(true);
    try {
      const updated = await renameWallSet(uploadSummary.wall_set_id, label);
      setWallSetLabel(updated.label);
      setEditingName(false);
    } catch (e) {
      handleAuthAwareError(e, setError);
    } finally {
      setSavingName(false);
    }
  }

  const query = catalogSearch.trim().toLowerCase();
  const filteredTypes = query
    ? squishyTypes.filter((t2) => t2.name.toLowerCase().includes(query))
    : squishyTypes;
  const filteredDeactivated = query
    ? deactivatedTypes.filter((t2) => t2.name.toLowerCase().includes(query))
    : deactivatedTypes;

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
            <button type="submit" disabled={!newTypeName.trim()}>
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

          {(squishyTypes.length > 0 || showDeactivated) && (
            <div className="catalog-search">
              <label htmlFor="catalog-search">{t("wallBuilder.catalogSearchLabel")}</label>
              <input
                id="catalog-search"
                type="text"
                placeholder={t("wallBuilder.catalogSearchPlaceholder")}
                value={catalogSearch}
                onChange={(e) => setCatalogSearch(e.target.value)}
              />
            </div>
          )}

          {squishyTypes.length > 0 &&
            (filteredTypes.length > 0 ? (
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
                  {filteredTypes.map((t2) => (
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
            ) : (
              <p className="muted">{t("wallBuilder.catalogNoMatches", { query: catalogSearch.trim() })}</p>
            ))}

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
              ) : filteredDeactivated.length === 0 ? (
                <p className="muted">{t("wallBuilder.catalogNoMatches", { query: catalogSearch.trim() })}</p>
              ) : (
                <table className="table-dimmed">
                  <thead>
                    <tr>
                      <th>{t("wallBuilder.tableName")}</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredDeactivated.map((t2) => (
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
            <FileDropzone
              id="csv-file"
              label={t("wallBuilder.csvLabel")}
              ext=".csv"
              accept=".csv"
              file={csvFile}
              onFile={setCsvFile}
              rejectMessage={t("wallBuilder.dropRejectCsv")}
            />
            <FileDropzone
              id="pdf-file"
              label={t("wallBuilder.pdfLabel")}
              ext=".pdf"
              accept=".pdf"
              file={pdfFile}
              onFile={setPdfFile}
              rejectMessage={t("wallBuilder.dropRejectPdf")}
            />
            <button type="submit" disabled={uploading || !csvFile || !pdfFile}>
              {uploading ? t("wallBuilder.uploading") : t("wallBuilder.uploadButton")}
            </button>
          </form>

          {uploadSummary && (
            <div className="upload-result">
              <p className="muted">{t("wallBuilder.createdWallSet", { id: uploadSummary.wall_set_id })}</p>

              <div className="wall-set-name">
                <label>{t("wallBuilder.wallSetNameLabel")}</label>
                {editingName ? (
                  <div className="row">
                    <input
                      type="text"
                      autoFocus
                      value={nameDraft}
                      onChange={(e) => setNameDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          handleSaveName();
                        } else if (e.key === "Escape") {
                          setEditingName(false);
                        }
                      }}
                    />
                    <button type="button" onClick={handleSaveName} disabled={savingName || !nameDraft.trim()}>
                      {savingName ? t("wallBuilder.savingName") : t("wallBuilder.saveName")}
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => setEditingName(false)}
                      disabled={savingName}
                    >
                      {t("wallBuilder.cancel")}
                    </button>
                  </div>
                ) : (
                  <div className="row">
                    <strong className="wall-set-name-value">{wallSetLabel}</strong>
                    <button
                      type="button"
                      className="quiet"
                      onClick={() => {
                        setNameDraft(wallSetLabel);
                        setEditingName(true);
                      }}
                    >
                      {t("wallBuilder.rename")}
                    </button>
                  </div>
                )}
              </div>

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

              {uploadSummary.type_breakdown.length > 0 && (
                <div className="type-breakdown">
                  <h3>{t("wallBuilder.breakdownHeading")}</h3>
                  <table>
                    <thead>
                      <tr>
                        <th>{t("wallBuilder.tableName")}</th>
                        <th className="num">{t("wallBuilder.breakdownQuantity")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {uploadSummary.type_breakdown.map((b) => (
                        <tr key={b.squishy_type_id}>
                          <td>{b.name}</td>
                          <td className="num">{b.total_quantity}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
