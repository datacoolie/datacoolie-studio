import { ArrowLeft, Check, Copy, Database, GitBranch, Maximize2, Minimize2, X } from "lucide-react";
import { Fragment, useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { useDrawerEscape } from "../../shared/hooks/useDrawerEscape";
import type { LineageDataflowFocusTarget } from "../../shared/lineageNavigation";
import { metadataNavigationTarget, type MetadataNavigationTarget } from "../../shared/metadataNavigation";
import type { MetadataDataflowField, MetadataDataflowRecord } from "./metadataDataflowModel";
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
  sourceFields,
} from "./metadataDataflowModel";
import { formatCellValue, formatPrettyStructuredValue, parseCellText, structuredCellKind } from "../metadata-explorer/metadataSheetOperations";
import { formatPrettySql, highlightStructuredValue } from "../metadata-explorer/MetadataStructuredCell";

interface MetadataDataflowDrawerProps {
  record: MetadataDataflowRecord;
  editable?: boolean;
  readOnly?: boolean;
  busy?: boolean;
  connectionRows?: Array<Record<string, unknown>>;
  connectionColumns?: Array<{ key: string }>;
  relatedDataflows?: MetadataDataflowRecord[];
  onSave?: (nextRow: Record<string, unknown>) => unknown | Promise<unknown>;
  onSaveDraft?: (nextRow: Record<string, unknown>) => unknown | Promise<unknown>;
  onValidate?: (nextRow: Record<string, unknown>) => unknown | Promise<unknown>;
  onBack?: () => void;
  onClose: () => void;
  onFocusInLineage?: (target: LineageDataflowFocusTarget) => void;
  onOpenMetadata?: (target: MetadataNavigationTarget) => void;
  onSelectRelatedDataflow?: (record: MetadataDataflowRecord) => void;
}

export function MetadataDataflowDrawer({
  record,
  editable = false,
  readOnly = false,
  busy = false,
  connectionRows = [],
  connectionColumns = [],
  relatedDataflows = [],
  onSave,
  onSaveDraft,
  onValidate,
  onBack,
  onClose,
  onFocusInLineage,
  onOpenMetadata,
  onSelectRelatedDataflow,
}: MetadataDataflowDrawerProps) {
  const [copied, setCopied] = useState(false);
  const [drawerMode, setDrawerMode] = useState<"view" | "edit">("view");
  const [drawerDraftRow, setDrawerDraftRow] = useState(record.row);
  const drawerTitleId = useId();
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const canEdit = drawerMode === "edit" && editable;
  const canToggleMode = editable && !readOnly;
  const drawerRecord: MetadataDataflowRecord = {
    ...record,
    row: drawerDraftRow,
    transformRows: record.transformRows.map((field) => ({
      ...field,
      value: drawerDraftRow[field.key],
      structured: isStructuredDataflowField(field.key, drawerDraftRow[field.key]),
    })),
  };
  const sourceFieldsForDisplay = sourceFields(drawerRecord);
  const destinationFieldsForDisplay = destinationFields(drawerRecord);
  const sourceConnection = connectionRow(connectionRows, formatCellValue(drawerDraftRow.source_connection_name) || record.source.connectionName);
  const destinationConnection = connectionRow(connectionRows, formatCellValue(drawerDraftRow.destination_connection_name) || record.destination.connectionName);
  const connectionOptions = connectionRows
    .map((row) => formatCellValue(row.name).trim())
    .filter(Boolean);
  const route = dataflowRouteText(drawerRecord);
  const related = relatedDataflowRecords(record, relatedDataflows);
  const drawerDirty = JSON.stringify(drawerDraftRow) !== JSON.stringify(record.row);

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
    const result = await handler(drawerDraftRow);
    if (result) setDrawerMode("view");
  }

  async function validateDrawerChanges() {
    if (!onValidate) return;
    await onValidate(drawerDraftRow);
  }

  function discardDrawerChanges() {
    setDrawerDraftRow(record.row);
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
            <small title={record.metadataSourceUri}>{record.metadataSourceName || record.metadataSourceUri || "metadata source"}</small>
            <div className="assets-drawer-header-chips dataflow-drawer-chips">
              <span className="assets-header-chip">row:{record.rowIndex + 1}</span>
              <span className="assets-header-chip">{record.stage || "not set"}</span>
              <span className="assets-header-chip">{record.processingMode || "batch"}</span>
              <span className={`assets-header-chip${record.isActive ? " is-active" : " is-inactive"}`}>
                {record.isActive ? "active" : "inactive"}
              </span>
              {!canToggleMode ? (
                <span className="assets-header-chip">view only</span>
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
                  {onSave ? <button className="text-action primary" type="button" onClick={() => void saveDrawerChanges("save")} disabled={!drawerDirty || busy}>Save</button> : null}
                  {onSaveDraft ? <button className="text-action" type="button" onClick={() => void saveDrawerChanges("draft")} disabled={!drawerDirty || busy}>Draft</button> : null}
                  {onValidate ? <button className="text-action" type="button" onClick={() => void validateDrawerChanges()} disabled={busy}>Validate</button> : null}
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
            <DataflowFields fields={dataflowFields(drawerRecord)} record={drawerRecord} editable={canEdit} onFieldChange={setField} />
            <RelatedDataflows records={related} onSelect={onSelectRelatedDataflow} />
          </section>

          <section className="assets-drawer-section dataflow-drawer-section dataflow-drawer-section-source">
            <DataflowFields
              fields={sourceFieldsForDisplay}
              record={drawerRecord}
              editable={canEdit}
              onFieldChange={setField}
              connectionOptions={connectionOptions}
              connectionDetails={connectionDetailFields(sourceFieldsForDisplay, "source", sourceConnection, connectionColumns)}
            />
          </section>

          <section className="assets-drawer-section dataflow-drawer-section dataflow-drawer-section-transform">
            <DataflowFields fields={drawerRecord.transformRows} record={drawerRecord} editable={canEdit} onFieldChange={setField} />
          </section>

          <section className="assets-drawer-section dataflow-drawer-section dataflow-drawer-section-destination">
            <DataflowFields
              fields={destinationFieldsForDisplay}
              record={drawerRecord}
              editable={canEdit}
              onFieldChange={setField}
              connectionOptions={connectionOptions}
              connectionDetails={connectionDetailFields(destinationFieldsForDisplay, "destination", destinationConnection, connectionColumns)}
            />
          </section>
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
  record,
  editable,
  onFieldChange,
  connectionOptions = [],
  connectionDetails,
  emptyText = "No fields.",
}: {
  fields: Array<{ key: string; label: string; value: unknown; structured: boolean }>;
  record: MetadataDataflowRecord;
  editable: boolean;
  onFieldChange: (key: string, value: unknown) => void;
  connectionOptions?: string[];
  connectionDetails?: MetadataDataflowField[];
  emptyText?: string;
}) {
  if (!fields.length) return <div className="assets-empty-inline">{emptyText}</div>;
  return (
    <dl className="assets-detail-list dataflow-field-list">
      {fields.map((field) => (
        <Fragment key={field.key}>
          <EditableDetailRow
            fieldKey={field.key}
            label={field.key}
            value={field.value}
            editable={editable}
            structured={field.structured}
            connectionOptions={connectionOptions}
            onChange={onFieldChange}
          />
          {isConnectionNameField(field.key) && formatCellValue(field.value) && connectionDetails?.length ? (
            <ConnectionDetailRows fields={connectionDetails} />
          ) : null}
        </Fragment>
      ))}
    </dl>
  );
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
      <pre className={`dataflow-structured-value${compact ? " is-compact" : ""}`}>
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
