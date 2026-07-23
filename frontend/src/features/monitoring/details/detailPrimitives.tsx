import { ArrowLeft, ArrowRight, Boxes, BriefcaseBusiness, Check, ChevronRight, Clock3, Copy, FileText, SearchCheck, Workflow, X } from "lucide-react";
import { isValidElement, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { MonitoringRecord } from "../../../shared/api/domainTypes";
import { useDrawerEscape } from "../../../shared/hooks/useDrawerEscape";
import { formatTimestampForDisplay, hasExplicitTimezone, isTimestampFieldName } from "../../../shared/time";
import { lifecycleStatusFromField, lifecycleStatusPresentation, type LifecycleStatus } from "../../../shared/statusPresentation";
import { LineageFormatIcon } from "../../lineage/components/LineageFormatIcon";
import { DataTable, StatusCell, display, formatBytes, formatNumber, formatSeconds, num, type TableSort } from "../MonitoringCharts";
import {
  diagnosticsCategoryLabel,
  diagnosticsEvidenceItems,
  diagnosticsInvestigationActions,
  diagnosticsLinkedJobRow,
  diagnosticsRuleDescription,
  diagnosticsSeverityPresentation,
} from "../diagnosticsPresentation";
import { formatMaintenanceLag, maintenanceFormatIconKind, maintenanceTableHealthClass, maintenanceTableHealthLabel, maintenanceTableHealthTone } from "../maintenancePresentation";
import { formatPhasePercent, monitoringEndpointPresentation, TablePager } from "../components/monitoringPrimitives";
import { SystemLogViewer } from "../SystemLogViewer";

export type DetailRow = [label: string, value: unknown, field?: string];

export type SemanticIntent = "success" | "failed" | "skipped" | "running" | "pending" | "bad" | "neutral";

export type DataflowPhaseKey = "source" | "transform" | "destination" | "overhead";

export type SemanticValueModel =
  | { kind: "status"; value: string }
  | { kind: "count"; value: number; intent?: SemanticIntent }
  | { kind: "reconciliation"; status: string; mismatch: number }
  | { kind: "text"; value: string; intent?: SemanticIntent };

export const SQL_BLOCK_FIELDS = new Set([
  "source_query",
  "source_filter_expression",
]);

export const LIST_BLOCK_FIELDS = new Set([
  "source_watermark_columns",
  "transformers_applied",
  "destination_merge_keys",
]);

export const JSON_BLOCK_FIELDS = new Set([
  "configure",
  "source_action",
  "source_configure",
  "source_watermark_before",
  "source_watermark_after",
  "source_watermark_effective",
  "transform_deduplicate_columns",
  "transform_latest_data_columns",
  "transform_filter_expression",
  "transform_additional_columns",
  "transform_schema_hints",
  "transform_configure",
  "destination_partition_columns",
  "destination_configure",
  "destination_operation_details",
]);

export function firstValue(row: Record<string, unknown>, fields: string[]) {
  for (const field of fields) {
    if (hasValue(row[field])) return row[field];
  }
  return null;
}

export function jobStatusTone(value: unknown) {
  const status = String(value ?? "unknown").trim().toLowerCase();
  if (status === "succeeded" || status === "success") return "is-succeeded";
  if (status === "failed" || status === "error") return "is-failed";
  if (status === "skipped" || status === "warning") return "is-skipped";
  if (status === "running") return "is-running";
  if (status === "pending") return "is-pending";
  return "is-unknown";
}

export function phaseRuntimeStatusClass(value: unknown) {
  return `monitoring-dataflow-runtime-card ${jobStatusTone(value)}`;
}

export function dataflowEndpointSummary(row: Record<string, unknown>, direction: "source" | "destination") {
  const endpoint = monitoringEndpointPresentation(row as MonitoringRecord, direction);
  return {
    asset: endpoint.locator,
    connection: endpoint.connection,
    format: endpoint.format || String(firstValue(row, [`${direction}_connection_type`]) || "unknown format"),
  };
}

export function DataflowRouteEndpoint({
  direction,
  endpoint,
}: {
  direction: "source" | "destination";
  endpoint: { asset: string; connection: string; format: string };
}) {
  return (
    <div className={`monitoring-dataflow-route-endpoint is-${direction}`}>
      <span>{direction}</span>
      <strong>{endpoint.asset}</strong>
      <small>{endpoint.connection} · {endpoint.format}</small>
    </div>
  );
}

export function MaintenanceHealthChip({ health, reason }: { health: unknown; reason?: unknown }) {
  return (
    <span className={`maintenance-table-health-chip ${maintenanceTableHealthClass(health)}`} title={String(reason ?? maintenanceTableHealthLabel(health))}>
      {maintenanceTableHealthLabel(health)}
    </span>
  );
}

export type FreshnessDrawerHealthTone = "success" | "warning" | "failed" | "neutral";

export function freshnessDrawerHealth(row: Record<string, unknown>): { label: string; tone: FreshnessDrawerHealthTone } {
  const hasEvidence = hasValue(row.latest_freshness_at) || hasValue(row.latest_run_at) || num(row, "run_count") > 0;
  if (!hasEvidence) return { label: "No evidence", tone: "neutral" };
  const latestStatus = String(firstValue(row, ["latest_run_status", "latest_freshness_status"]) ?? "").trim().toLowerCase();
  const watermarkState = String(firstValue(row, ["movement_state", "coverage_state"]) ?? "").trim().toLowerCase();
  if (latestStatus === "failed" || watermarkState === "invalid") return { label: "Needs review", tone: "failed" };
  if (["running", "pending"].includes(latestStatus) || watermarkState === "incomplete") return { label: "Needs review", tone: "warning" };
  const ageDays = Number(row.age_days ?? (Number(row.age_seconds) / 86_400));
  if (Number.isFinite(ageDays) && ageDays > 7) return { label: "Stale", tone: "warning" };
  return { label: "Current", tone: "success" };
}

export function FreshnessIdentitySection({ row }: { row: Record<string, unknown> }) {
  const source = dataflowEndpointSummary(row, "source");
  const destination = dataflowEndpointSummary(row, "destination");
  const context = [row.stage, row.operation_type, row.processing_mode, row.destination_load_type]
    .filter(hasValue)
    .map(String);
  return (
    <section className="monitoring-detail-section monitoring-freshness-identity">
      <div className="monitoring-dataflow-route-card">
        <DataflowRouteEndpoint direction="source" endpoint={source} />
        <ArrowRight className="monitoring-dataflow-route-arrow" size={18} aria-hidden="true" />
        <DataflowRouteEndpoint direction="destination" endpoint={destination} />
        <div className="monitoring-freshness-dataflow-id">
          <span>Dataflow ID</span>
          <strong>{String(row.dataflow_id ?? "-")}</strong>
        </div>
        {context.length ? (
          <div className="monitoring-dataflow-route-context">
            {context.map((value, index) => <span key={`${value}-${index}`}>{value}</span>)}
          </div>
        ) : null}
      </div>
    </section>
  );
}

export function FreshnessRunTimeCell({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  const [start, end] = freshnessRunTimeLines(row, timezoneName);
  return (
    <span
      className="freshness-run-stack-cell"
      title={[
        `Start: ${start}`,
        `End: ${end.slice(2)}`,
      ].join("\n")}
    >
      <strong>{start}</strong>
      <small><span>{end}</span></small>
    </span>
  );
}

export function freshnessRunTimeLines(row: Record<string, unknown>, timezoneName?: string | null) {
  const start = formatTimestampForDisplay(row.start_time, timezoneName, "-");
  const end = formatTimestampForDisplay(row.end_time, timezoneName, "-");
  return [start, `→ ${end}`];
}

export function formatFreshnessAge(ageSeconds: unknown, ageDays: unknown) {
  const seconds = Number(ageSeconds);
  if (Number.isFinite(seconds)) {
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.round((seconds / 3600) * 10) / 10}h`;
    return `${Math.round((seconds / 86400) * 10) / 10}d`;
  }
  const days = Number(ageDays);
  return Number.isFinite(days) ? `${Math.round(days * 10) / 10}d` : "-";
}

export function DataflowPhaseContribution({ row }: { row: Record<string, unknown> }) {
  const segments = dataflowPhaseSegments(row);
  if (!segments.length) return <span className="monitor-muted">-</span>;
  const title = segments
    .map((segment) => `${phaseLabel(segment.phase)}: ${formatSeconds(segment.value)} (${formatPhasePercent(segment.percent)})`)
    .join("\n");
  return (
    <div className="monitoring-phase-contribution" title={title}>
      <div className="monitoring-phase-stack" aria-label="Dataflow phase contribution">
        {segments.map((segment) => (
          <i key={segment.phase} className={`phase-${segment.phase}`} style={{ flex: `0 0 ${segment.percent}%` }} />
        ))}
      </div>
    </div>
  );
}

export function dataflowPhaseBottleneck(row: Record<string, unknown>) {
  const phaseHealth = String(row.phase_health ?? "").toLowerCase();
  for (const phase of DATAFLOW_PHASE_KEYS) {
    if (phaseHealth.includes(phase)) return { phase, label: phaseLabel(phase) };
  }
  const segments = dataflowPhaseDurations(row);
  const maxSegment = segments.sort((left, right) => right.value - left.value)[0];
  return maxSegment && maxSegment.value > 0 ? { phase: maxSegment.phase, label: phaseLabel(maxSegment.phase) } : null;
}

export const DATAFLOW_PHASE_KEYS: DataflowPhaseKey[] = ["source", "transform", "destination", "overhead"];

export function dataflowPhaseSegments(row: Record<string, unknown>) {
  const visible = dataflowPhaseDurations(row).filter((segment) => segment.value > 0);
  const total = visible.reduce((sum, segment) => sum + segment.value, 0);
  if (total <= 0) return [];
  let usedPercent = 0;
  return visible.map((segment, index) => {
    const percent = index === visible.length - 1 ? Math.max(0, 100 - usedPercent) : (segment.value / total) * 100;
    usedPercent += percent;
    return { ...segment, percent };
  });
}

export function dataflowPhaseDurations(row: Record<string, unknown>) {
  const source = Math.max(0, num(row, "source_duration_seconds"));
  const transform = Math.max(0, num(row, "transform_duration_seconds"));
  const destination = Math.max(0, num(row, "destination_duration_seconds"));
  const overhead = Math.max(0, optionalNum(row, "overhead_duration_seconds") ?? 0);
  return [
    { phase: "source" as const, value: source },
    { phase: "transform" as const, value: transform },
    { phase: "destination" as const, value: destination },
    { phase: "overhead" as const, value: overhead },
  ];
}

export function phaseLabel(phase: DataflowPhaseKey) {
  if (phase === "source") return "Source";
  if (phase === "transform") return "Transform";
  if (phase === "destination") return "Destination";
  return "Overhead";
}

export function ErrorMessageBlock({ value }: { value: unknown }) {
  return (
    <div className="monitoring-error-message-body">
      {isValidElement(value) ? value : String(value ?? "-")}
    </div>
  );
}

export function GroupedDetailCard({
  title,
  rows,
  timezoneName,
  showEmpty = false,
  className,
}: {
  title: string;
  rows: DetailRow[];
  timezoneName?: string | null;
  showEmpty?: boolean;
  className?: string;
}) {
  const visibleRows = showEmpty ? rows : rows.filter(([, value]) => hasValue(value));
  if (!visibleRows.length) return null;
  return (
    <div className={`monitoring-job-group-card${className ? ` ${className}` : ""}`}>
      <span>{title}</span>
      <dl>
        {visibleRows.map(([label, value, field]) => {
          const isBlock = isGroupedBlockValue(value, field);
          return (
          <div key={label} className={[isBlock ? "is-block-value" : "", isErrorField(field ?? "") ? "is-error-value" : ""].filter(Boolean).join(" ") || undefined}>
            <dt>{label}</dt>
            <dd>{renderGroupedValue(value, timezoneName, field)}</dd>
          </div>
          );
        })}
      </dl>
    </div>
  );
}

export function renderGroupedValue(value: unknown, timezoneName?: string | null, field?: string): ReactNode {
  if (isValidElement(value)) return value;
  if (!hasValue(value)) return "-";
  if (isSemanticValue(value)) return <SemanticValue value={value} />;
  if (typeof value === "string" && field && timezoneName && isTimestampFieldName(field)) {
    return isRuntimeTimestampField(field)
      ? formatRuntimeTimestampForDisplay(value, timezoneName)
      : formatTimestampForDisplay(value, timezoneName);
  }
  if (field && SQL_BLOCK_FIELDS.has(field)) return <CodeBlock value={value} kind="sql" />;
  if (field && LIST_BLOCK_FIELDS.has(field)) return <CodeBlock value={value} kind="list" />;
  if (field && JSON_BLOCK_FIELDS.has(field)) return <CodeBlock value={value} kind="json" />;
  if (field?.endsWith("duration_seconds")) return formatSeconds(Number(value) || 0);
  if (field?.includes("bytes")) return formatBytes(Number(value) || 0);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value) || (value && typeof value === "object")) return <JsonBlock value={value} />;
  if (typeof value === "string" && looksJson(value)) return <JsonBlock value={value} />;
  if (typeof value === "number") return <SemanticValue value={semanticNumber(field, value)} />;
  if (typeof value === "string") return <SemanticValue value={semanticText(field, value)} />;
  return value as ReactNode;
}

export function isRuntimeTimestampField(field: string) {
  return /^(source|transform|destination)_(start|end)_time$/u.test(field);
}

export function formatRuntimeTimestampForDisplay(value: unknown, timezoneName?: string | null) {
  const rawValue = String(value ?? "").trim();
  if (!rawValue) return "-";
  const normalized = hasExplicitTimezone(rawValue) ? rawValue : `${rawValue}Z`;
  return formatTimestampForDisplay(normalized, timezoneName);
}

export function isGroupedBlockValue(value: unknown, field?: string) {
  if (!hasValue(value) || isValidElement(value) || isSemanticValue(value)) return false;
  if (field && (SQL_BLOCK_FIELDS.has(field) || LIST_BLOCK_FIELDS.has(field) || JSON_BLOCK_FIELDS.has(field))) return true;
  if (Array.isArray(value) || (value && typeof value === "object")) return true;
  return typeof value === "string" && looksJson(value);
}

export function isSemanticValue(value: unknown): value is SemanticValueModel {
  return Boolean(value && typeof value === "object" && "kind" in value);
}

export function semanticNumber(field: string | undefined, value: number): SemanticValueModel {
  const lowerField = String(field ?? "").toLowerCase();
  let intent: SemanticIntent = "neutral";
  const lifecycleStatus = lifecycleStatusFromField(lowerField);
  if (lifecycleStatus) intent = semanticStatusIntent(lifecycleStatus);
  if (lowerField.includes("success") && !lifecycleStatus) intent = "success";
  if (lowerField.includes("error") && !lifecycleStatus) intent = value > 0 ? "failed" : "neutral";
  if (lowerField.includes("mismatch")) intent = value > 0 ? "failed" : "neutral";
  return { kind: "count", value, intent };
}

export function semanticText(field: string | undefined, value: string): SemanticValueModel {
  const lowerValue = value.toLowerCase();
  const lowerField = String(field ?? "").toLowerCase();
  if (lowerField === "status" || ["succeeded", "failed", "skipped", "running", "pending"].includes(lowerValue)) {
    return { kind: "status", value };
  }
  if (lowerField.includes("reconciliation") || lowerValue === "matched" || lowerValue === "mismatch") {
    return { kind: "text", value, intent: lowerValue === "matched" ? "success" : lowerValue === "mismatch" ? "failed" : "neutral" };
  }
  return { kind: "text", value };
}

export function SemanticValue({ value }: { value: SemanticValueModel }) {
  if (value.kind === "reconciliation") {
    const statusIntent = value.status.toLowerCase() === "matched" ? "success" : value.status.toLowerCase() === "mismatch" ? "failed" : "neutral";
    const countIntent = value.mismatch > 0 ? "failed" : "neutral";
    return (
      <span className="monitoring-semantic-pair">
        <span className={`monitoring-semantic-value is-${statusIntent}`} style={semanticIntentStyle(statusIntent)}>{value.status}</span>
        <span aria-hidden="true">·</span>
        <span className={`monitoring-semantic-value is-${countIntent}`} style={semanticIntentStyle(countIntent)}>{display({ value: value.mismatch }, "value")}</span>
      </span>
    );
  }
  if (value.kind === "count") {
    const intent = value.intent ?? "neutral";
    return <span className={`monitoring-semantic-value is-${intent}`} style={semanticIntentStyle(intent)}>{display({ value: value.value }, "value")}</span>;
  }
  const intent = semanticIntent(value);
  return <span className={`monitoring-semantic-value is-${intent}`} style={semanticIntentStyle(intent)}>{value.value}</span>;
}

export function semanticIntent(value: SemanticValueModel) {
  if ("intent" in value && value.intent) return value.intent;
  if (value.kind !== "status") return "neutral";
  const normalized = value.value.toLowerCase();
  if (normalized === "succeeded") return "success";
  if (normalized === "failed") return "failed";
  if (normalized === "skipped") return "skipped";
  if (normalized === "running") return "running";
  if (normalized === "pending") return "pending";
  return "neutral";
}

export function semanticStatusIntent(status: LifecycleStatus): SemanticIntent {
  return status === "succeeded" ? "success" : status;
}

export function semanticIntentStyle(intent: SemanticIntent): CSSProperties | undefined {
  const status = intent === "success" ? "succeeded" : intent === "bad" ? "failed" : intent;
  const presentation = lifecycleStatusPresentation(status);
  return presentation ? { color: presentation.textColor } : undefined;
}

export function CodeBlock({ value, kind }: { value: unknown; kind: "json" | "list" | "sql" }) {
  const [copied, setCopied] = useState(false);
  const formatted = kind === "sql" ? formatSqlBlock(value) : kind === "list" ? formatListBlock(value) : formatCompactJsonBlock(value);
  const isJsonLike = kind !== "sql";
  return (
    <div className={`monitoring-code-box monitoring-code-box-${kind}`}>
      <button
        className="icon-action small monitoring-json-copy"
        type="button"
        aria-label={`Copy ${kind.toUpperCase()}`}
        title={`Copy ${kind.toUpperCase()}`}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          void copyToClipboard(formatted, setCopied);
        }}
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
      </button>
      <pre className="monitoring-code-block">
        {isJsonLike ? highlightJson(formatted) : formatted}
      </pre>
    </div>
  );
}

export function JsonBlock({ value, compactArray = false }: { value: unknown; compactArray?: boolean }) {
  const [copied, setCopied] = useState(false);
  const formatted = formatJsonValue(value, compactArray);
  return (
    <div className="monitoring-json-box monitoring-inline-json-box">
      <button
        className="icon-action small monitoring-json-copy"
        type="button"
        aria-label="Copy JSON"
        title="Copy JSON"
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          void copyToClipboard(formatted, setCopied);
        }}
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
      </button>
      <pre className="monitoring-inline-json monitoring-json-light">{highlightJson(formatted)}</pre>
    </div>
  );
}

export function safeJsonStringify(value: unknown) {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function formatJsonValue(value: unknown, compactArray = false) {
  if (compactArray) return formatCompactJsonArray(value);
  return typeof value === "string" && looksJson(value) ? formatJson(value) : safeJsonStringify(value);
}

export function formatCompactJsonBlock(value: unknown) {
  const parsed = parseJsonLike(value);
  if (Array.isArray(parsed)) return formatCompactJsonArray(parsed);
  if (parsed && typeof parsed === "object") return formatCompactJsonObject(parsed as Record<string, unknown>);
  if (typeof value === "string" && looksJson(value)) return formatJson(value);
  return String(value);
}

export function formatListBlock(value: unknown) {
  const parsed = parseJsonLike(value);
  if (Array.isArray(parsed)) return formatCompactJsonArray(parsed);
  if (typeof value === "string") {
    const values = value.split(",").map((item) => item.trim()).filter(Boolean);
    if (values.length > 1) return formatCompactJsonArray(values);
  }
  return formatCompactJsonBlock(value);
}

export function formatSqlBlock(value: unknown) {
  const sql = String(value).trim().replace(/\s+/g, " ");
  if (!sql) return "-";
  return sql
    .replace(/\b(left join|right join|inner join|outer join|union all|group by|order by|from|where|join|having|union)\b/giu, "\n$1")
    .replace(/\b(and|or)\b/giu, "\n  $1")
    .replace(/\s*,\s*/gu, ",\n  ")
    .trim();
}

export function parseJsonLike(value: unknown) {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!looksJson(trimmed)) return value;
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

export function formatCompactJsonObject(value: Record<string, unknown>) {
  const entries = Object.entries(value);
  if (!entries.length) return "{}";
  const lines: string[] = ["{"];
  entries.forEach(([key, item], index) => {
    const suffix = index + 1 < entries.length ? "," : "";
    lines.push(`  ${JSON.stringify(key)}: ${JSON.stringify(item)}${suffix}`);
  });
  lines.push("}");
  return lines.join("\n");
}

export function formatCompactJsonArray(value: unknown) {
  let parsed = value;
  if (typeof value === "string") {
    try {
      parsed = JSON.parse(value);
    } catch {
      return value;
    }
  }
  if (!Array.isArray(parsed)) return safeJsonStringify(parsed);
  if (!parsed.length) return "[]";
  if (parsed.every(isPrimitiveJsonValue)) {
    return `[\n  ${parsed.map((item) => JSON.stringify(item)).join(", ")}\n]`;
  }
  return JSON.stringify(parsed, null, 2);
}

export function isPrimitiveJsonValue(value: unknown) {
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

export function highlightJson(value: string) {
  const tokens = value.match(/"(?:\\.|[^"\\])*"(?=\s*:)|"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\b(?:true|false|null)\b|[{}\[\],:]/gu);
  if (!tokens) return value;

  const nodes: ReactNode[] = [];
  let cursor = 0;

  tokens.forEach((token, index) => {
    const start = value.indexOf(token, cursor);
    if (start < cursor) return;
    if (start > cursor) nodes.push(value.slice(cursor, start));
    nodes.push(
      <span key={`${start}-${index}`} className={`json-token ${jsonTokenClass(token, value.slice(start + token.length))}`}>
        {token}
      </span>
    );
    cursor = start + token.length;
  });

  if (cursor < value.length) nodes.push(value.slice(cursor));
  return nodes;
}

export function jsonTokenClass(token: string, afterToken: string) {
  if (/^"/u.test(token) && afterToken.trimStart().startsWith(":")) return "json-key";
  if (/^"/u.test(token)) return "json-string";
  if (/^-?\d/u.test(token)) return "json-number";
  if (token === "true" || token === "false") return "json-boolean";
  if (token === "null") return "json-null";
  return "json-punctuation";
}

export function detailValue(row: Record<string, unknown>, field: string, timezoneName?: string | null) {
  const value = row[field];
  if (field.endsWith("duration_seconds")) return `${display(row, field)}s`;
  if (field.includes("bytes")) return formatBytes(num(row, field));
  if (typeof value === "string" && timezoneName && isTimestampFieldName(field)) return formatTimestampForDisplay(value, timezoneName);
  if (Array.isArray(value) || (value && typeof value === "object")) {
    return <JsonBlock value={value} />;
  }
  if (typeof value === "string" && looksJson(value)) {
    return <JsonBlock value={value} />;
  }
  return display(row, field);
}

export function isErrorField(field: string) {
  const normalized = field.toLowerCase();
  return normalized.includes("error") || normalized.includes("issue") || normalized === "last_error";
}

export function IssueCell({ row }: { row: MonitoringRecord }) {
  const issue = String(row.error_preview || row.error_message || row.source_error_message || row.transform_error_message || row.destination_error_message || "");
  if (!issue) return <span className="monitor-muted">-</span>;
  const status = String(row.status || "").toLowerCase();
  return (
    <span className={`monitoring-issue-cell${status === "failed" ? " is-error" : ""}`} title={issue} aria-label={issue}>
      {issue}
    </span>
  );
}

export async function copyToClipboard(value: string, setCopied: (copied: boolean) => void) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
    } else {
      fallbackCopyToClipboard(value);
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  } catch {
    try {
      fallbackCopyToClipboard(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  }
}

export function fallbackCopyToClipboard(value: string) {
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

export function hasValue(value: unknown) {
  if (Array.isArray(value)) return value.length > 0;
  return value !== null && value !== undefined && value !== "";
}

export function optionalNum(row: Record<string, unknown>, field: string) {
  const value = row[field];
  if (!hasValue(value)) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function looksJson(value: string) {
  const trimmed = value.trim();
  return (trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"));
}

export function formatJson(value: string) {
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

export function humanize(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}
