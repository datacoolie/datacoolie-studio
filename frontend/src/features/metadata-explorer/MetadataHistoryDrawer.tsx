import { FileClock, RotateCcw, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { MetadataBackup, MetadataEditorDocument } from "../../shared/api/domainTypes";
import { useDrawerEscape } from "../../shared/hooks/useDrawerEscape";
import { formatCellValue } from "./metadataSheetOperations";
import type { SheetKey } from "./metadataSheetTypes";

interface MetadataHistoryDrawerProps {
  currentDocument: MetadataEditorDocument;
  dirty: boolean;
  onClear: () => Promise<void>;
  onClose: () => void;
  onDelete: (backupId: number) => Promise<void>;
  onList: () => Promise<MetadataBackup[]>;
  onPreview: (backupId: number) => Promise<MetadataEditorDocument>;
  onRestore: (backup: MetadataBackup) => Promise<void>;
}

const sheets = [
  { key: "connections", label: "Connections" },
  { key: "dataflows", label: "Dataflows" },
  { key: "schema_hints", label: "Schema hints" }
] satisfies Array<{ key: SheetKey; label: string }>;

export function MetadataHistoryDrawer({
  currentDocument,
  dirty,
  onClear,
  onClose,
  onDelete,
  onList,
  onPreview,
  onRestore
}: MetadataHistoryDrawerProps) {
  const [backups, setBackups] = useState<MetadataBackup[]>([]);
  const [selectedBackup, setSelectedBackup] = useState<MetadataBackup | null>(null);
  const [preview, setPreview] = useState<MetadataEditorDocument | null>(null);
  const [activeSheet, setActiveSheet] = useState<SheetKey>("dataflows");
  const [loading, setLoading] = useState(true);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void loadBackups();
  }, [currentDocument.source.source_id]);

  useDrawerEscape(onClose, !actionBusy);

  async function loadBackups() {
    setLoading(true);
    setError("");
    try {
      const items = await onList();
      setBackups(items);
      if (selectedBackup && !items.some((item) => item.id === selectedBackup.id)) {
        setSelectedBackup(null);
        setPreview(null);
      }
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setLoading(false);
    }
  }

  async function selectBackup(backup: MetadataBackup) {
    setSelectedBackup(backup);
    setPreview(null);
    setPreviewLoading(true);
    setError("");
    try {
      setPreview(await onPreview(backup.id));
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setPreviewLoading(false);
    }
  }

  async function restoreSelected() {
    if (!selectedBackup) return;
    const unsavedNotice = dirty ? "\n\nUnsaved editor changes will be discarded." : "";
    if (!window.confirm(`Restore this metadata backup?\n\n${formatDateTime(selectedBackup.created_at)}${unsavedNotice}\n\nThe current file will be backed up first.`)) return;
    setActionBusy(true);
    setError("");
    try {
      await onRestore(selectedBackup);
      onClose();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setActionBusy(false);
    }
  }

  async function deleteSelected() {
    if (!selectedBackup) return;
    if (!window.confirm(`Delete this backup permanently?\n\n${selectedBackup.backup_path}`)) return;
    setActionBusy(true);
    setError("");
    try {
      await onDelete(selectedBackup.id);
      setSelectedBackup(null);
      setPreview(null);
      await loadBackups();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setActionBusy(false);
    }
  }

  async function clearHistory() {
    if (!backups.length) return;
    if (!window.confirm(
      `Delete all ${backups.length} metadata backups permanently?\n\nThis cannot be undone. The current metadata file will not be changed.`
    )) return;
    setActionBusy(true);
    setError("");
    try {
      await onClear();
      setBackups([]);
      setSelectedBackup(null);
      setPreview(null);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setActionBusy(false);
    }
  }

  const previewSheet = preview?.sheets[activeSheet];
  const previewColumns = previewSheet?.columns ?? [];
  const previewRows = previewSheet?.rows ?? [];

  return (
    <div className="metadata-drawer-backdrop" onMouseDown={actionBusy ? undefined : onClose}>
      <aside className="metadata-drawer metadata-history-drawer" aria-label="Metadata save history" onMouseDown={(event) => event.stopPropagation()}>
        <header className="metadata-drawer-header">
          <div>
            <span className="eyebrow">Metadata source</span>
            <h2>Save history</h2>
            <small title={currentDocument.source.uri}>{shortPath(currentDocument.source.uri)}</small>
          </div>
          <div className="metadata-history-header-actions">
            <button
              className="metadata-history-clear-button"
              type="button"
              onClick={() => void clearHistory()}
              disabled={loading || actionBusy || !backups.length}
            >
              <Trash2 size={14} />
              Clear history
            </button>
            <button className="icon-action small" type="button" onClick={onClose} disabled={actionBusy} title="Close history">
              <X size={16} />
            </button>
          </div>
        </header>

        {error ? <div className="metadata-history-error" role="alert">{error}</div> : null}

        <div className="metadata-history-layout">
          <section className="metadata-history-timeline" aria-label="Backup versions">
            <div className="metadata-current-version">
              <FileClock size={16} />
              <div>
                <strong>Current version</strong>
                <span>{revisionLabel(currentDocument.source.revision)}</span>
              </div>
            </div>
            <div className="metadata-history-list">
              {backups.map((backup) => (
                <button
                  key={backup.id}
                  type="button"
                  className={`metadata-history-item${selectedBackup?.id === backup.id ? " active" : ""}`}
                  onClick={() => void selectBackup(backup)}
                >
                  <strong>{formatDateTime(backup.created_at)}</strong>
                  <span>{revisionLabel(backup.source_revision)}</span>
                  <small title={backup.backup_path}>{shortPath(backup.backup_path)}</small>
                </button>
              ))}
              {loading ? <div className="table-empty">Loading history...</div> : null}
              {!loading && !backups.length ? <div className="table-empty">No backups yet</div> : null}
            </div>
          </section>

          <section className="metadata-history-preview" aria-label="Backup preview">
            {selectedBackup ? (
              <>
                <div className="metadata-history-preview-header">
                  <div>
                    <strong>{formatDateTime(selectedBackup.created_at)}</strong>
                    <span>Snapshot before save</span>
                  </div>
                  <div className="metadata-history-actions">
                    <button className="secondary-button" type="button" onClick={() => void restoreSelected()} disabled={actionBusy || previewLoading}>
                      <RotateCcw size={14} />
                      Restore
                    </button>
                    <button className="danger-button" type="button" onClick={() => void deleteSelected()} disabled={actionBusy}>
                      <Trash2 size={14} />
                      Delete
                    </button>
                  </div>
                </div>
                <nav className="tabs metadata-history-tabs" aria-label="Backup preview sheets">
                  {sheets.map((sheet) => (
                    <button
                      key={sheet.key}
                      type="button"
                      className={activeSheet === sheet.key ? "active" : ""}
                      onClick={() => setActiveSheet(sheet.key)}
                    >
                      {sheet.label}
                      <span>{preview?.sheets[sheet.key]?.rows.length ?? 0}</span>
                    </button>
                  ))}
                </nav>
                <div className="metadata-history-table-wrap">
                  {previewLoading ? <div className="table-empty">Loading preview...</div> : null}
                  {!previewLoading && preview ? (
                    <table className="metadata-history-table">
                      <thead>
                        <tr>
                          {previewColumns.map((column) => <th key={column.key}>{column.name}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {previewRows.map((row, rowIndex) => (
                          <tr key={rowIndex}>
                            {previewColumns.map((column) => (
                              <td key={column.key} title={formatCellValue(row[column.key])}>
                                {formatCellValue(row[column.key])}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : null}
                  {!previewLoading && preview && !previewRows.length ? <div className="table-empty">No rows</div> : null}
                </div>
              </>
            ) : (
              <div className="metadata-history-empty">
                <FileClock size={24} />
                <strong>Select a backup to preview</strong>
                <span>Backups are snapshots created before metadata saves.</span>
              </div>
            )}
          </section>
        </div>
      </aside>
    </div>
  );
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}

function revisionLabel(revision?: Record<string, unknown> | null) {
  const hash = String(revision?.content_hash ?? "");
  const size = Number(revision?.size);
  const parts = [hash ? hash.slice(0, 10) : "Unknown revision"];
  if (Number.isFinite(size)) parts.push(formatBytes(size));
  return parts.join(" · ");
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function shortPath(value: string) {
  return value.replace(/\\/g, "/").split("/").filter(Boolean).slice(-3).join("/");
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : "History action failed.";
}
