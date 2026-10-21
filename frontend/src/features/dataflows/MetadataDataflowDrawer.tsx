import { ArrowLeft, Check, Copy, Database, GitBranch, Maximize2, Minimize2, X } from "lucide-react";
import { Fragment, useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { formatPrettySql } from "../../shared/components/SqlSyntax";
import { useDrawerEscape } from "../../shared/hooks/useDrawerEscape";
import { displayStorageUri } from "../../shared/lib/storage";
import { api } from "../../shared/api/client";
import type { PendingSqlFileOperation } from "../../shared/api/domainTypes";
import { formatCellValue, formatPrettyStructuredValue, parseCellText, structuredCellKind } from "../../shared/metadata/metadataCellValues";
import type { MetadataDataflowField, MetadataDataflowRecord } from "../../shared/metadata/metadataDataflowModel";
import { highlightStructuredValue } from "../../shared/metadata/metadataStructuredValue";
import {
  dataflowFields,
  dataflowRouteLoadType,
  dataflowRouteText,
  dataflowTitle,
  destinationFields,
  displayDataflowValue,
  isStructuredDataflowField,
  labelFromKey,
  routeEndpointParts,
  sameSqlFileReference,
  sourceFields,
} from "../../shared/metadata/metadataDataflowModel";
import type { LineageDataflowFocusTarget } from "../../shared/lineageNavigation";
import { metadataNavigationTarget, type MetadataNavigationTarget } from "../../shared/metadataNavigation";

type SqlDraftSnapshot = {
  mode: "inline" | "file";
  path: string;
  content: string;
  revision: Record<string, unknown> | null;
  fileUri: string | null;
  baseUri: string | null;
  baseKind: string | null;
  configRevision: Record<string, unknown> | null;
};

interface MetadataDataflowDrawerProps {
  record: MetadataDataflowRecord;
  environmentId?: number;
  pendingSqlFiles?: PendingSqlFileOperation[];
  sectionOrder?: "source-transform-destination" | "source-destination-transform";
  editable?: boolean;
  readOnly?: boolean;
  busy?: boolean;
  connectionRows?: Array<Record<string, unknown>>;
  connectionColumns?: Array<{ key: string }>;
  relatedDataflows?: MetadataDataflowRecord[];
  onSave?: (nextRow: Record<string, unknown>, sqlOperation?: PendingSqlFileOperation | null) => unknown | Promise<unknown>;
  onSaveDraft?: (nextRow: Record<string, unknown>, sqlOperation?: PendingSqlFileOperation | null) => unknown | Promise<unknown>;
  onBack?: () => void;
  onClose: () => void;
  onFocusInLineage?: (target: LineageDataflowFocusTarget) => void;
  onOpenMetadata?: (target: MetadataNavigationTarget) => void;
  onSelectRelatedDataflow?: (record: MetadataDataflowRecord) => void;
}

export function MetadataDataflowDrawer({
  record,
  environmentId,
  pendingSqlFiles = [],
  sectionOrder = "source-transform-destination",
  editable = false,
  readOnly = false,
  busy = false,
  connectionRows = [],
  connectionColumns = [],
  relatedDataflows = [],
  onSave,
  onSaveDraft,
  onBack,
  onClose,
  onFocusInLineage,
  onOpenMetadata,
  onSelectRelatedDataflow,
}: MetadataDataflowDrawerProps) {
  const [copied, setCopied] = useState(false);
  const [drawerMode, setDrawerMode] = useState<"view" | "edit">("view");
  const [drawerDraftRow, setDrawerDraftRow] = useState<Record<string, unknown>>(record.row);
  const [sqlMode, setSqlMode] = useState<"inline" | "file">("inline");
  const [sqlFilePath, setSqlFilePath] = useState("");
  const [sqlContent, setSqlContent] = useState("");
  const [sqlFileUri, setSqlFileUri] = useState<string | null>(null);
  const [sqlBaseUri, setSqlBaseUri] = useState<string | null>(null);
  const [sqlBaseKind, setSqlBaseKind] = useState<string | null>(null);
  const [sqlFileRevision, setSqlFileRevision] = useState<Record<string, unknown> | null>(null);
  const [sqlConfigRevision, setSqlConfigRevision] = useState<Record<string, unknown> | null>(null);
  const [sqlLoading, setSqlLoading] = useState(false);
  const [sqlError, setSqlError] = useState<string | null>(null);
  const [sqlBaseline, setSqlBaseline] = useState<SqlDraftSnapshot>({
    mode: "inline",
    path: "",
    content: "",
    revision: null,
    fileUri: null,
    baseUri: null,
    baseKind: null,
    configRevision: null,
  });
  const drawerTitleId = useId();
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const canEdit = drawerMode === "edit" && editable;
  const canToggleMode = editable;
  const declaredSourceQuery = typeof(record.row.source_query) === "string"
    ? record.row.source_query.trim()
    : "";
  const effectiveSourceQuery = sqlMode === "file" ? sqlFilePath.trim() : sqlContent;
  const effectiveDrawerRow: Record<string, unknown> = { ...drawerDraftRow, source_query: effectiveSourceQuery };
  const drawerRecord: MetadataDataflowRecord = {
    ...record,
    row: effectiveDrawerRow,
    transformRows: record.transformRows.map((field) => ({
      ...field,
      value: effectiveDrawerRow[field.key],
      structured: isStructuredDataflowField(field.key, effectiveDrawerRow[field.key]),
    })),
  };
  const sourceFieldsForDisplay = sourceFields(drawerRecord);
  const destinationFieldsForDisplay = destinationFields(drawerRecord);
  const sourceConnection = connectionRow(connectionRows, formatCellValue(effectiveDrawerRow["source_connection_name"]) || record.source.connectionName);
  const destinationConnection = connectionRow(connectionRows, formatCellValue(effectiveDrawerRow["destination_connection_name"]) || record.destination.connectionName);
  const connectionOptions = connectionRows
    .map((row) => formatCellValue(row.name).trim())
    .filter(Boolean);
  const route = dataflowRouteText(drawerRecord);
  const related = relatedDataflowRecords(record, relatedDataflows);
  const sqlSnapshot = {
    mode: sqlMode,
    path: sqlFilePath,
    content: sqlContent,
    revision: sqlFileRevision,
  };
  const sqlDirty = JSON.stringify(sqlSnapshot) !== JSON.stringify({
    mode: sqlBaseline.mode,
    path: sqlBaseline.path,
    content: sqlBaseline.content,
    revision: sqlBaseline.revision,
  });
  const sqlPathNeedsLoad = sqlMode === "file"
    && Boolean(sqlFilePath.trim())
    && !sameSqlFileReference(sqlFilePath, sqlBaseline.path);
  const drawerDirty = JSON.stringify(effectiveDrawerRow) !== JSON.stringify(record.row) || sqlDirty;
  const pendingSqlFingerprint = JSON.stringify(pendingSqlFiles);
  const pendingSqlOperation = sqlMode === "file"
    ? pendingSqlFiles.find((operation) =>
      operation.source_id === record.metadataSourceId
      && sameSqlFileReference(operation.query, sqlFilePath)
    ) ?? null
    : null;
  const sqlDraftStatus = pendingSqlOperation?.publish_status === "applied"
    ? "partial"
    : pendingSqlOperation
      ? pendingSqlOperation.base_revision ? "modified" : "new"
      : null;

  function handleSqlPathChange(path: string) {
    const changed = path.trim() !== sqlFilePath.trim();
    setSqlFilePath(path);
    setSqlBaseKind(path.trim().startsWith("artifact:/") ? "project_root" : path.trim() ? "sql_root" : null);
    setSqlError(null);
    if (changed) {
      setSqlFileUri(null);
      setSqlBaseUri(null);
      setSqlFileRevision(null);
      setSqlConfigRevision(null);
    }
  }

  const sourceSection = (
    <section key="source" className="assets-drawer-section dataflow-drawer-section dataflow-drawer-section-source">
      <DataflowFields
        fields={sourceFieldsForDisplay}
        editable={canEdit}
        onFieldChange={setField}
        connectionOptions={connectionOptions}
        sqlEditor={{
          mode: sqlMode,
          path: sqlFilePath,
          content: sqlContent,
          fileUri: sqlFileUri,
          baseUri: sqlBaseUri,
          baseKind: sqlBaseKind,
          draftStatus: sqlDraftStatus,
          loading: sqlLoading,
          error: sqlError,
          needsLoad: sqlPathNeedsLoad,
          editable: canEdit,
          onModeChange: setSqlMode,
          onPathChange: handleSqlPathChange,
          onContentChange: setSqlContent,
          onLoadExisting: () => void loadExistingSqlFile(),
        }}
        connectionDetails={connectionDetailFields(sourceFieldsForDisplay, "source", sourceConnection, connectionColumns)}
      />
    </section>
  );
  const transformSection = (
    <section key="transform" className="assets-drawer-section dataflow-drawer-section dataflow-drawer-section-transform">
      <DataflowFields fields={drawerRecord.transformRows} editable={canEdit} onFieldChange={setField} />
    </section>
  );
  const destinationSection = (
    <section key="destination" className="assets-drawer-section dataflow-drawer-section dataflow-drawer-section-destination">
      <DataflowFields
        fields={destinationFieldsForDisplay}
        editable={canEdit}
        onFieldChange={setField}
        connectionOptions={connectionOptions}
        connectionDetails={connectionDetailFields(destinationFieldsForDisplay, "destination", destinationConnection, connectionColumns)}
      />
    </section>
  );
  const routeSections = sectionOrder === "source-destination-transform"
    ? [sourceSection, destinationSection, transformSection]
    : [sourceSection, transformSection, destinationSection];

  useDrawerEscape(onClose);

  useEffect(() => {
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusFrame = window.requestAnimationFrame(() => closeButtonRef.current?.focus());
    return () => {
      window.cancelAnimationFrame(focusFrame);
      const rowTrigger = Array.from(document.querySelectorAll<HTMLElement>(".metadata-grid-row-number-button"))
        .find((element) => element.textContent?.trim() === String(record.rowIndex + 1));
      const previousFocus = returnFocusRef.current;
      const target = previousFocus && previousFocus !== document.body && previousFocus.isConnected
        ? previousFocus
        : rowTrigger;
      window.requestAnimationFrame(() => target?.focus());
    };
  }, [record.rowIndex]);

  useEffect(() => {
    setDrawerDraftRow(record.row);
    setDrawerMode("view");
  }, [record.key, record.row]);

  useEffect(() => {
    let cancelled = false;
    const pending = pendingSqlFiles.find((operation) =>
      operation.source_id === record.metadataSourceId
      && sameSqlFileReference(operation.query, declaredSourceQuery)
    );
    const fileReference = isSqlFileReference(declaredSourceQuery) || Boolean(pending);
    const path = pending?.query ?? (fileReference ? declaredSourceQuery : "");
    const baseKind = pending?.base_kind ?? (path.startsWith("artifact:/") ? "project_root" : fileReference ? "sql_root" : null);
    setSqlMode(fileReference ? "file" : "inline");
    setSqlFilePath(path);
    setSqlFileUri(null);
    setSqlBaseUri(pending?.base_uri ?? null);
    setSqlBaseKind(baseKind);
    setSqlError(null);
    setSqlFileRevision(pending?.base_revision ?? null);
    setSqlConfigRevision(pending?.config_revision ?? null);
    if (!fileReference) {
      setSqlLoading(false);
      setSqlContent(declaredSourceQuery);
      setSqlBaseline({
        mode: "inline",
        path: "",
        content: declaredSourceQuery,
        revision: null,
        fileUri: null,
        baseUri: null,
        baseKind: null,
        configRevision: null,
      });
      return () => { cancelled = true; };
    }
    if (pending) {
      setSqlLoading(false);
      setSqlContent(pending.content);
      setSqlBaseline({
        mode: "file",
        path,
        content: pending.content,
        revision: pending.base_revision ?? null,
        fileUri: null,
        baseUri: pending.base_uri ?? null,
        baseKind: pending.base_kind ?? baseKind,
        configRevision: pending.config_revision ?? null,
      });
      return () => { cancelled = true; };
    }
    if (!environmentId || !record.metadataSourceId) {
      setSqlLoading(false);
      setSqlContent("");
      setSqlBaseline({
        mode: "file",
        path,
        content: "",
        revision: null,
        fileUri: null,
        baseUri: null,
        baseKind,
        configRevision: null,
      });
      return () => { cancelled = true; };
    }
    setSqlLoading(true);
    setSqlContent("");
    void api.readSqlReference(environmentId, record.metadataSourceId, path)
      .then((resolved) => {
        if (cancelled) return;
        setSqlContent(resolved.content);
        setSqlFileUri(resolved.file_uri ?? null);
        setSqlBaseUri(resolved.base_uri ?? null);
        setSqlBaseKind(resolved.base_kind ?? baseKind);
        setSqlFileRevision(resolved.revision ?? null);
        setSqlConfigRevision(resolved.config_revision ?? null);
        setSqlBaseline({
          mode: "file",
          path,
          content: resolved.content,
          revision: resolved.revision ?? null,
          fileUri: resolved.file_uri ?? null,
          baseUri: resolved.base_uri ?? null,
          baseKind: resolved.base_kind ?? baseKind,
          configRevision: resolved.config_revision ?? null,
        });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setSqlError(error instanceof Error ? error.message : "SQL file could not be read");
        setSqlBaseline({
          mode: "file",
          path,
          content: "",
          revision: null,
          fileUri: null,
          baseUri: null,
          baseKind,
          configRevision: null,
        });
      })
      .finally(() => {
        if (!cancelled) setSqlLoading(false);
      });
    return () => { cancelled = true; };
  }, [declaredSourceQuery, environmentId, pendingSqlFingerprint, record.key, record.metadataSourceId]);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1200);
    return () => window.clearTimeout(timer);
  }, [copied]);

  async function copyIdentity() {
    if (!navigator.clipboard?.writeText) return;
    await navigator.clipboard.writeText(record.dataflowId || record.name);
    setCopied(true);
  }

  function setField(key: string, value: unknown) {
    setDrawerDraftRow((current) => ({ ...current, [key]: value }));
  }

  async function loadExistingSqlFile() {
    const path = sqlFilePath.trim();
    if (!path) {
      setSqlError("Enter a relative .sql file path first.");
      return;
    }
    if (!environmentId || !record.metadataSourceId) {
      setSqlError("This SQL file cannot be loaded without a metadata source.");
      return;
    }
    const pending = pendingSqlFiles.find((operation) =>
      operation.source_id === record.metadataSourceId
      && sameSqlFileReference(operation.query, path)
    );
    if (pending) {
      setSqlContent(pending.content);
      setSqlFileUri(null);
      setSqlBaseUri(pending.base_uri ?? null);
      setSqlBaseKind(pending.base_kind ?? sqlBaseKind);
      setSqlFileRevision(pending.base_revision ?? null);
      setSqlConfigRevision(pending.config_revision ?? null);
      setSqlError(null);
      setSqlBaseline({
        mode: "file",
        path,
        content: pending.content,
        revision: pending.base_revision ?? null,
        fileUri: null,
        baseUri: pending.base_uri ?? null,
        baseKind: pending.base_kind ?? sqlBaseKind,
        configRevision: pending.config_revision ?? null,
      });
      return;
    }
    setSqlLoading(true);
    setSqlError(null);
    try {
      const resolved = await api.readSqlReference(environmentId, record.metadataSourceId, path);
      setSqlContent(resolved.content);
      setSqlFileUri(resolved.file_uri ?? null);
      setSqlBaseUri(resolved.base_uri ?? null);
      setSqlBaseKind(resolved.base_kind ?? sqlBaseKind);
      setSqlFileRevision(resolved.revision ?? null);
      setSqlConfigRevision(resolved.config_revision ?? null);
      setSqlBaseline({
        mode: "file",
        path,
        content: resolved.content,
        revision: resolved.revision ?? null,
        fileUri: resolved.file_uri ?? null,
        baseUri: resolved.base_uri ?? null,
        baseKind: resolved.base_kind ?? sqlBaseKind,
        configRevision: resolved.config_revision ?? null,
      });
    } catch (error: unknown) {
      setSqlError(error instanceof Error ? error.message : "SQL file could not be read");
    } finally {
      setSqlLoading(false);
    }
  }

  function currentSqlOperation(): PendingSqlFileOperation | null {
    if (sqlMode !== "file" || !sqlFilePath.trim() || !record.metadataSourceId) return null;
    const existing = pendingSqlFiles.find((operation) =>
      operation.source_id === record.metadataSourceId
      && sameSqlFileReference(operation.query, sqlFilePath.trim())
    );
    if (!sqlDirty && !existing) return null;
    const retainsPublishedState = Boolean(existing && existing.content === sqlContent);
    return {
      ...(retainsPublishedState ? existing : {}),
      source_id: record.metadataSourceId,
      query: sqlFilePath.trim(),
      content: sqlContent,
      base_revision: sqlFileRevision ?? existing?.base_revision ?? null,
      base_kind: sqlBaseKind ?? undefined,
      base_uri: sqlBaseUri ?? existing?.base_uri ?? null,
      config_revision: sqlConfigRevision ?? existing?.config_revision ?? null,
      publish_status: retainsPublishedState ? existing?.publish_status : "pending",
      published_revision: retainsPublishedState ? existing?.published_revision ?? null : null,
      published_content_hash: retainsPublishedState ? existing?.published_content_hash ?? null : null,
    };
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLElement>) {
    if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) {
      event.stopPropagation();
      if (event.key !== "Tab") return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
      "button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [href], [tabindex]:not([tabindex=\"-1\"])",
    ));
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  async function saveDrawerChanges(action: "save" | "draft") {
    const handler = action === "save" ? onSave : onSaveDraft;
    if (!handler || !drawerDirty) return;
    if (sqlMode === "file" && !sqlFilePath.trim()) {
      setSqlError("Enter a relative .sql file path, or switch to Inline SQL.");
      return;
    }
    const result = await handler(effectiveDrawerRow, currentSqlOperation());
    if (result) setDrawerMode("view");
  }

  function discardDrawerChanges() {
    setDrawerDraftRow(record.row);
    setSqlMode(sqlBaseline.mode);
    setSqlFilePath(sqlBaseline.path);
    setSqlContent(sqlBaseline.content);
    setSqlFileUri(sqlBaseline.fileUri);
    setSqlBaseUri(sqlBaseline.baseUri);
    setSqlBaseKind(sqlBaseline.baseKind);
    setSqlFileRevision(sqlBaseline.revision);
    setSqlConfigRevision(sqlBaseline.configRevision);
    setSqlError(null);
    setSqlLoading(false);
    setDrawerMode("view");
  }

  return createPortal(
    <div className="metadata-drawer-backdrop dataflow-detail-backdrop" onMouseDown={onClose}>
      <aside
        className="metadata-drawer dataflow-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={drawerTitleId}
        onKeyDown={handleKeyDown}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="metadata-drawer-header dataflow-drawer-header">
          {onBack ? (
            <button className="icon-action small dataflow-drawer-back-icon" type="button" title="Back" aria-label="Back" onClick={onBack}>
              <ArrowLeft size={14} />
            </button>
          ) : null}
          <div className="dataflow-drawer-title">
            <span className="eyebrow">Dataflow</span>
            <div className="dataflow-drawer-title-line">
              <h2 id={drawerTitleId} title={dataflowTitle(record)}>{dataflowTitle(record)}</h2>
              <button className={`icon-action small assets-copy-icon${copied ? " copied" : ""}`} type="button" title={copied ? "Copied" : "Copy id"} aria-label={copied ? "Copied" : "Copy dataflow id"} onClick={() => void copyIdentity()}>
                {copied ? <Check size={14} /> : <Copy size={14} />}
              </button>
            </div>
            <small title={displayStorageUri(record.metadataSourceUri)}>{displayStorageUri(record.metadataSourceName || record.metadataSourceUri) || "metadata source"}</small>
            <div className="assets-drawer-header-chips dataflow-drawer-chips">
              <span className="assets-header-chip">row:{record.rowIndex + 1}</span>
              <span className="assets-header-chip">{record.stage || "not set"}</span>
              <span className="assets-header-chip">{record.processingMode || "batch"}</span>
              <span className={`assets-header-chip${record.isActive ? " is-active" : " is-inactive"}`}>
                {record.isActive ? "active" : "inactive"}
              </span>
              {!canToggleMode ? (
                <span className="assets-header-chip">view only</span>
              ) : readOnly ? (
                <span className="assets-header-chip">draft only · source read-only</span>
              ) : null}
            </div>
          </div>
          <div className="assets-drawer-header-actions dataflow-drawer-header-actions">
            <div className="dataflow-header-mode-actions">
              {canToggleMode ? (
                <div className="dataflow-mode-switch" role="group" aria-label="Dataflow drawer mode">
                  <button
                    type="button"
                    className={drawerMode === "view" ? "active" : ""}
                    aria-pressed={drawerMode === "view"}
                    onClick={() => setDrawerMode("view")}
                  >
                    View
                  </button>
                  <button
                    type="button"
                    className={drawerMode === "edit" ? "active" : ""}
                    aria-pressed={drawerMode === "edit"}
                    onClick={() => setDrawerMode("edit")}
                  >
                    Edit
                  </button>
                </div>
              ) : null}
              {canEdit ? (
                <div className="dataflow-edit-actions dataflow-edit-header-actions">
                  {onSave && !readOnly ? <button className="text-action primary" type="button" onClick={() => void saveDrawerChanges("save")} disabled={!drawerDirty || busy}>Save to source</button> : null}
                  {onSaveDraft ? <button className="text-action" type="button" onClick={() => void saveDrawerChanges("draft")} disabled={!drawerDirty || busy}>Save draft</button> : null}
                  <button className="text-action" type="button" onClick={discardDrawerChanges} disabled={!drawerDirty || busy}>Discard</button>
                </div>
              ) : null}
            </div>
            <button ref={closeButtonRef} className="icon-action small" type="button" title="Close" aria-label="Close dataflow details" onClick={onClose}>
              <X size={14} />
            </button>
          </div>
        </header>

        <div className="metadata-drawer-body dataflow-drawer-body">
          <section className="dataflow-route-card" aria-label="Dataflow route" title={route}>
            <span>Route</span>
            <RouteText record={drawerRecord} />
          </section>

          {record.issues.length ? (
            <section className="assets-drawer-section">
              <h3>Issues</h3>
              <ul className="assets-list assets-issues-list">
                {record.issues.map((issue, index) => (
                  <li key={`${issue.column}-${index}`}>
                    <strong className={`assets-severity assets-severity-${issue.severity}`}>{issue.severity}</strong>
                    <span>{issue.column ? `${issue.column}: ${issue.message}` : issue.message}</span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <section className="assets-drawer-section dataflow-drawer-section dataflow-drawer-section-dataflow">
            <DataflowFields fields={dataflowFields(drawerRecord)} editable={canEdit} onFieldChange={setField} />
            <RelatedDataflows records={related} onSelect={onSelectRelatedDataflow} />
          </section>

          {routeSections}
        </div>

        {onFocusInLineage || onOpenMetadata ? (
        <footer className="assets-drawer-footer dataflow-drawer-footer">
          {onFocusInLineage ? (
            <button
              className="text-action primary"
              type="button"
              onClick={() => onFocusInLineage({
                metadataSourceId: record.metadataSourceId,
                dataflowId: record.dataflowId,
                name: record.name,
              })}
            >
              <GitBranch size={14} />
              Focus In Lineage
            </button>
          ) : null}
          {onOpenMetadata ? (
            <button
              className="text-action"
              type="button"
              onClick={() => onOpenMetadata(metadataNavigationTarget([record.dataflowId], record.name || record.dataflowId))}
            >
              <Database size={14} />
              Open in Metadata
            </button>
          ) : null}
        </footer>
        ) : null}
      </aside>
    </div>,
    document.body,
  );
}

function connectionRow(rows: Array<Record<string, unknown>>, connectionName: string) {
  const normalizedName = connectionName.trim().toLocaleLowerCase();
  if (!normalizedName) return undefined;
  return rows.find((row) => formatCellValue(row.name).trim().toLocaleLowerCase() === normalizedName);
}

function isSqlFileReference(value: string) {
  return value.startsWith("artifact:/") || value.toLocaleLowerCase().endsWith(".sql");
}

function RouteText({ record }: { record: MetadataDataflowRecord }) {
  const loadType = dataflowRouteLoadType(record);
  return (
    <strong className="metadata-dataflow-inline-route">
      <RouteEndpointText record={record} prefix="source" tone="source" />
      <span className="metadata-dataflow-inline-route-divider"> → </span>
      <RouteEndpointText record={record} prefix="destination" tone="destination" />
      {loadType ? <span className="metadata-dataflow-inline-route-load"> : {loadType}</span> : null}
    </strong>
  );
}

function RouteEndpointText({
  record,
  prefix,
  tone,
}: {
  record: MetadataDataflowRecord;
  prefix: "source" | "destination";
  tone: "source" | "destination";
}) {
  const { connectionName, locator, kind } = routeEndpointParts(record, prefix);
  const routeLocator = kind === "sql_query" ? "SQL query" : locator;
  return (
    <span className={`metadata-dataflow-inline-route-endpoint is-${tone}${kind === "sql_query" ? " is-sql-query" : ""}`}>
      <span className="metadata-dataflow-inline-route-connection">{connectionName || "-"}</span>
      {routeLocator ? <><span className="metadata-dataflow-inline-route-separator"> - </span><span className="metadata-dataflow-inline-route-locator">{routeLocator}</span></> : null}
    </span>
  );
}

function relatedDataflowRecords(record: MetadataDataflowRecord, records: MetadataDataflowRecord[]) {
  const sourceKey = endpointKey(record.source);
  const destinationKey = endpointKey(record.destination);
  return records.filter((candidate) => {
    if (candidate.key === record.key) return false;
    const candidateSource = endpointKey(candidate.source);
    const candidateDestination = endpointKey(candidate.destination);
    return (sourceKey && (candidateSource === sourceKey || candidateDestination === sourceKey))
      || (destinationKey && (candidateSource === destinationKey || candidateDestination === destinationKey));
  }).slice(0, 8);
}

function endpointKey(endpoint: MetadataDataflowRecord["source"]) {
  if (endpoint.assetId) return `asset:${endpoint.assetId}`;
  return [endpoint.connectionName, endpoint.schemaName, endpoint.table, endpoint.path].filter(Boolean).join(".");
}

function connectionDetailFields(
  fields: MetadataDataflowField[],
  prefix: "source" | "destination",
  connection?: Record<string, unknown>,
  connectionColumns: Array<{ key: string }> = [],
) {
  if (connection) {
    const columnKeys = connectionColumns.map((column) => column.key);
    const keys = [...new Set([...columnKeys, ...Object.keys(connection)])]
      .filter((key) => !key.startsWith("__"));
    const orderedKeys = [
      ...(keys.includes("connection_id") ? ["connection_id"] : []),
      ...keys.filter((key) => key !== "connection_id"),
    ];
    return orderedKeys
      .filter((key) => formatCellValue(connection[key]).trim())
      .map((key) => ({
        key,
        label: labelFromKey(key),
        value: connection[key],
        structured: isStructuredDataflowField(key, connection[key]),
      }));
  }
  const detailKeys = new Set([
    `${prefix}_connection_type`,
    `${prefix}_format`,
    `${prefix}_catalog`,
    `${prefix}_database`,
  ]);
  return fields.filter((field) => detailKeys.has(field.key) && formatCellValue(field.value).trim());
}

function DataflowFields({
  fields,
  editable,
  onFieldChange,
  connectionOptions = [],
  sqlEditor,
  connectionDetails,
  emptyText = "No fields.",
}: {
  fields: Array<{ key: string; label: string; value: unknown; structured: boolean }>;
  editable: boolean;
  onFieldChange: (key: string, value: unknown) => void;
  connectionOptions?: string[];
  sqlEditor?: SqlEditorProps;
  connectionDetails?: MetadataDataflowField[];
  emptyText?: string;
}) {
  if (!fields.length) return <div className="assets-empty-inline">{emptyText}</div>;
  return (
    <dl className="assets-detail-list dataflow-field-list">
      {fields.map((field) => (
        <Fragment key={field.key}>
          {field.key === "source_query" && sqlEditor ? (
            <SqlQueryField state={sqlEditor} />
          ) : (
            <EditableDetailRow
              fieldKey={field.key}
              label={field.key}
              value={field.value}
              editable={editable}
              structured={field.structured}
              connectionOptions={connectionOptions}
              onChange={onFieldChange}
            />
          )}
          {isConnectionNameField(field.key) && formatCellValue(field.value) && connectionDetails?.length ? (
            <ConnectionDetailRows fields={connectionDetails} />
          ) : null}
        </Fragment>
      ))}
    </dl>
  );
}

type SqlEditorProps = {
  mode: "inline" | "file";
  path: string;
  content: string;
  fileUri: string | null;
  baseUri: string | null;
  baseKind: string | null;
  draftStatus: "new" | "modified" | "partial" | null;
  loading: boolean;
  error: string | null;
  needsLoad: boolean;
  editable: boolean;
  onModeChange: (mode: "inline" | "file") => void;
  onPathChange: (path: string) => void;
  onContentChange: (content: string) => void;
  onLoadExisting: () => void;
};

function SqlQueryField({ state }: { state: SqlEditorProps }) {
  const origin = state.fileUri
    || (!state.needsLoad && state.baseUri ? joinDisplayUri(state.baseUri, state.path) : "")
    || (!state.needsLoad ? state.path : "");
  const originLabel = state.baseKind === "project_root" ? "project root" : "SQL root";
  return (
    <div className="dataflow-edit-row dataflow-edit-row-block dataflow-sql-query-row">
      <dt>SQL query</dt>
      <dd>
        {state.editable ? (
          <div className="dataflow-sql-editor">
            <div className="dataflow-sql-mode" role="group" aria-label="SQL storage mode">
              <button
                type="button"
                className={state.mode === "inline" ? "active" : ""}
                aria-pressed={state.mode === "inline"}
                onClick={() => state.onModeChange("inline")}
              >
                Inline SQL
              </button>
              <button
                type="button"
                className={state.mode === "file" ? "active" : ""}
                aria-pressed={state.mode === "file"}
                onClick={() => state.onModeChange("file")}
              >
                SQL file
              </button>
            </div>
            {state.mode === "file" ? (
              <>
                {state.draftStatus ? <DraftSqlStatus status={state.draftStatus} /> : null}
                <div className="dataflow-sql-path-row">
                  <input
                    aria-label="SQL file path"
                    value={state.path}
                    placeholder="queries/orders.sql"
                    onChange={(event) => state.onPathChange(event.target.value)}
                  />
                  <button
                    className="text-action"
                    type="button"
                    onClick={state.onLoadExisting}
                    disabled={state.loading || !state.path.trim()}
                  >
                    {state.loading ? "Loading…" : "Load existing"}
                  </button>
                </div>
                <small className="dataflow-sql-help">
                  Relative to the SQL root. Use <code>artifact:/...</code> for a project-root file.
                </small>
                {state.needsLoad ? (
                  <span className="dataflow-sql-status dataflow-sql-status-warning">
                    Path changed. Existing content is not loaded; the current text will be staged at this path.
                  </span>
                ) : null}
                {origin ? (
                  <div className="dataflow-sql-origin" title={origin}>
                    <span>Source: {originLabel}</span>
                    <code>{displayStorageUri(origin)}</code>
                  </div>
                ) : null}
              </>
            ) : null}
            {state.loading ? <span className="dataflow-sql-status">Reading SQL file…</span> : null}
            {state.error ? <span className="dataflow-sql-error">{state.error}</span> : null}
            <textarea
              aria-label={state.mode === "file" ? "SQL file content" : "Inline SQL query"}
              value={state.content}
              spellCheck={false}
              placeholder="SELECT ..."
              onChange={(event) => state.onContentChange(event.target.value)}
            />
          </div>
        ) : (
          <div className="dataflow-sql-preview">
            {state.mode === "file" ? (
              <div className="dataflow-sql-origin" title={origin || state.path}>
                {state.draftStatus ? <DraftSqlStatus status={state.draftStatus} /> : null}
                <span>Loaded from {originLabel}</span>
                <code>{displayStorageUri(origin || state.path)}</code>
              </div>
            ) : <span className="dataflow-sql-mode-label">Inline SQL</span>}
            {state.loading ? <span className="dataflow-sql-status">Reading SQL file…</span> : null}
            {state.error ? <span className="dataflow-sql-error">{state.error}</span> : null}
            {state.content ? (
              <pre className="dataflow-structured-value sql-syntax-light"><code>{highlightStructuredValue(formatPrettySql(state.content), "sql")}</code></pre>
            ) : <span className="assets-empty-inline">No SQL content.</span>}
          </div>
        )}
      </dd>
    </div>
  );
}

function DraftSqlStatus({ status }: { status: "new" | "modified" | "partial" }) {
  const label = status === "new"
    ? "New file · Draft only"
    : status === "modified"
      ? "Modified · Draft only"
      : "Partially published · retry required";
  return <span className="dataflow-sql-draft-status">{label}</span>;
}

function joinDisplayUri(baseUri: string, relativePath: string) {
  return `${baseUri.replace(/[\\/]+$/, "")}/${relativePath.replace(/\\/g, "/")}`;
}

function RelatedDataflows({
  records,
  onSelect,
}: {
  records: MetadataDataflowRecord[];
  onSelect?: (record: MetadataDataflowRecord) => void;
}) {
  return (
    <div className="dataflow-related-dataflows">
      <span>Related dataflows</span>
      {records.length ? (
        <div>
          {records.map((related) => (
            <button
              key={related.key}
              className="text-action"
              type="button"
              disabled={!onSelect}
              onClick={() => onSelect?.(related)}
              title={dataflowTitle(related)}
            >
              {dataflowTitle(related)}
            </button>
          ))}
        </div>
      ) : <em>None</em>}
    </div>
  );
}

function isConnectionNameField(key: string) {
  return key === "source_name"
    || key === "source_connection_name"
    || key === "destination_name"
    || key === "destination_connection_name";
}

function EditableDetailRow({
  fieldKey,
  label,
  value,
  editable,
  structured = isStructuredDataflowField(fieldKey, value),
  connectionOptions,
  onChange,
}: {
  fieldKey: string;
  label: string;
  value: unknown;
  editable: boolean;
  structured?: boolean;
  connectionOptions: string[];
  onChange: (key: string, value: unknown) => void;
}) {
  const formatted = formatCellValue(value);
  if (!editable || fieldKey === "dataflow_id") {
    return <DetailRow
      label={label}
      value={value}
      fieldKey={fieldKey}
      structured={structured}
      code={fieldKey === "dataflow_id"}
    />;
  }
  return (
    <div className={structured ? "dataflow-edit-row dataflow-edit-row-block" : "dataflow-edit-row"}>
      <dt>{label}</dt>
      <dd>
        {fieldKey === "is_active" ? (
          <label className="dataflow-toggle">
            <input
              type="checkbox"
              checked={value !== false}
              onChange={(event) => onChange(fieldKey, event.target.checked)}
            />
          </label>
        ) : structured ? (
          <textarea
            value={formatted}
            spellCheck={false}
            placeholder={structuredFieldPlaceholder(fieldKey)}
            onChange={(event) => onChange(fieldKey, parseCellText(event.target.value))}
          />
        ) : isConnectionNameField(fieldKey) ? (
          <ConnectionNameInput
            value={formatted}
            options={connectionOptions}
            onChange={(nextValue) => onChange(fieldKey, parseCellText(nextValue))}
          />
        ) : (
          <input
            value={formatted}
            onChange={(event) => onChange(fieldKey, parseCellText(event.target.value))}
          />
        )}
      </dd>
    </div>
  );
}

function DetailRow({
  label,
  value,
  fieldKey,
  code = false,
  structured = false,
}: {
  label: string;
  value: unknown;
  fieldKey?: string;
  code?: boolean;
  structured?: boolean;
}) {
  const formatted = displayDataflowValue(value);
  return (
    <div>
      <dt>{label}</dt>
      <dd title={formatted}>
        {structured && formatCellValue(value) ? (
          <StructuredValue fieldKey={fieldKey ?? ""} value={value} />
        ) : code ? <code>{formatted}</code> : formatted}
      </dd>
    </div>
  );
}

function ConnectionDetailRows({ fields }: { fields: MetadataDataflowField[] }) {
  return (
    <>
      {fields.map((field) => (
        <div className="dataflow-connection-detail-row" key={field.key}>
          <dt>{field.key}</dt>
          <dd>{field.structured && formatCellValue(field.value)
            ? <StructuredValue fieldKey={field.key} value={field.value} />
            : displayDataflowValue(field.value)}</dd>
        </div>
      ))}
    </>
  );
}

function StructuredValue({ fieldKey, value }: { fieldKey: string; value: unknown }) {
  const [compact, setCompact] = useState(false);
  const [copied, setCopied] = useState(false);
  const structuredKind = structuredCellKind(fieldKey, value);
  const prettyValue = structuredKind === "sql"
    ? formatPrettySql(formatCellValue(value))
    : formatPrettyStructuredValue(value) || formatCellValue(value);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1200);
    return () => window.clearTimeout(timer);
  }, [copied]);

  async function copyValue() {
    if (!navigator.clipboard?.writeText) return;
    await navigator.clipboard.writeText(prettyValue);
    setCopied(true);
  }

  return (
    <div className="dataflow-structured-value-wrap">
      <div className="dataflow-structured-value-actions">
        <button
          className="icon-action small"
          type="button"
          title={compact ? "Expand height" : "Compact height"}
          aria-label={compact ? "Expand height" : "Compact height"}
          aria-expanded={!compact}
          onClick={() => setCompact((current) => !current)}
        >
          {compact ? <Maximize2 size={13} /> : <Minimize2 size={13} />}
        </button>
        <button
          className={`icon-action small${copied ? " copied" : ""}`}
          type="button"
          title={copied ? "Copied" : "Copy"}
          aria-label={copied ? "Copied" : "Copy"}
          onClick={() => void copyValue()}
        >
          {copied ? <Check size={13} /> : <Copy size={13} />}
        </button>
      </div>
      <pre className={`dataflow-structured-value sql-syntax-light${compact ? " is-compact" : ""}`}>
        <code>{highlightStructuredValue(prettyValue, structuredKind)}</code>
      </pre>
    </div>
  );
}

function structuredFieldPlaceholder(fieldKey: string) {
  if (fieldKey === "source_query" || fieldKey.endsWith("_filter_expression")) return "Enter SQL expression";
  if (fieldKey === "configure" || fieldKey.endsWith("_configure")) return '{ "option": "value" }';
  if (fieldKey === "transform_additional_columns") return '{ "new_column": "expression" }';
  if (fieldKey === "transform_schema_hints") return '{ "column_name": "string" }';
  if (fieldKey === "transform_rename_columns") return '{ "old_name": "new_name" }';
  if (fieldKey === "transform_value_rules") return '[{ "operation": "trim", "columns": ["column_name"] }]';
  if (fieldKey === "transform_hash_columns") return '[{ "target_column": "column_hash", "columns": ["column_name"] }]';
  if (fieldKey === "transform_masking_rules") return '[{ "method": "redact", "columns": ["column_name"], "value": "[PRIVATE]" }]';
  if (fieldKey === "destination_partition_columns") return '["partition_column"]';
  if (fieldKey.endsWith("_columns")) return '["column_name"]';
  return '{ "key": "value" }';
}

function ConnectionNameInput({
  value,
  options,
  onChange,
}: {
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  const [draftValue, setDraftValue] = useState(value);
  const [open, setOpen] = useState(false);

  useEffect(() => setDraftValue(value), [value]);

  const query = draftValue.trim().toLocaleLowerCase();
  const visibleOptions = query && query !== value.toLocaleLowerCase()
    ? options.filter((option) => option.toLocaleLowerCase().includes(query))
    : options;

  return (
    <div className="dataflow-connection-picker">
      <input
        value={draftValue}
        aria-expanded={open}
        aria-haspopup="listbox"
        onFocus={() => setOpen(true)}
        onClick={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        onChange={(event) => {
          setDraftValue(event.target.value);
          onChange(event.target.value);
          setOpen(true);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            setDraftValue(value);
            setOpen(false);
          }
          if (event.key === "Enter") setOpen(false);
        }}
      />
      {open ? (
        <div className="dataflow-connection-options" role="listbox">
          {visibleOptions.length ? visibleOptions.map((option) => (
            <button
              key={option}
              type="button"
              role="option"
              onMouseDown={(event) => {
                event.preventDefault();
                setDraftValue(option);
                onChange(option);
                setOpen(false);
              }}
            >
              {option}
            </button>
          )) : <span>No matching connection</span>}
        </div>
      ) : null}
    </div>
  );
}
