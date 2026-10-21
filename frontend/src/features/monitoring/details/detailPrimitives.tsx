import { ArrowRight, Check, Copy } from "lucide-react";
import { isValidElement, useState, type CSSProperties, type ReactNode } from "react";
import { formatPrettySql, highlightSql } from "../../../shared/components/SqlSyntax";
import type { MonitoringRecord } from "../../../shared/api/domainTypes";
import { formatTimestampForDisplay, hasExplicitTimezone, isTimestampFieldName } from "../../../shared/time";
import { lifecycleStatusFromField, lifecycleStatusPresentation, type LifecycleStatus } from "../../../shared/statusPresentation";
import { display, formatBytes, formatSeconds, num } from "../../../shared/components/DataTable";
import { maintenanceTableHealthClass, maintenanceTableHealthLabel } from "../maintenancePresentation";
import { CompactNumberValue, metricValue } from "../components/monitoringPresentation";
import { formatAgeNumber, formatExactNumber, formatTooltipBytes, formatTooltipNumber, isMetricInteger, toApproximateNumber, type MetricValue } from "../../../shared/lib/numberFormat";
import { formatRawRecordJson, isLosslessJsonNumber, parseRawRecordJson } from "../../../shared/api/monitoringAdapters";
import { stringify as losslessStringify } from "lossless-json";
import { formatPhasePercent, monitoringEndpointPresentation } from "../components/monitoringPresentation";
import { monitoringConnectionStageClass } from "../monitoringStagePresentation";
import { effectiveMonitoringMessage } from "../monitoringMessage";
import { monitoringDrawerRawDimensionLabel } from "../monitoringDimensions";
import { Help } from "../../../shared/components/Help";

export type DetailRow = [label: string, value: unknown, field?: string];

export type SemanticIntent = "success" | "failed" | "skipped" | "running" | "pending" | "bad" | "neutral";

export type DataflowPhaseKey = "source" | "transform" | "destination" | "overhead";

export type SemanticValueModel =
  | { kind: "status"; value: string }
  | { kind: "count"; value: MetricValue; intent?: SemanticIntent }
  | { kind: "reconciliation"; status: string; mismatch: MetricValue }
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
  "run_attributes",
  "transform_deduplicate_columns",
  "transform_latest_data_columns",
  "transform_filter_expression",
  "transform_additional_columns",
  "transform_schema_hints",
  "transform_select_columns",
  "transform_drop_columns",
  "transform_rename_columns",
  "transform_value_rules",
  "transform_hash_columns",
  "transform_masking_rules",
  "transform_configure",
  "destination_partition_columns",
  "destination_configure",
  "destination_operation_details",
  "adjustment_input_json",
  "adjustment_effective_json",
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
    asset: endpoint.locator === "Unknown asset" ? "unk" : monitoringDrawerRawDimensionLabel(endpoint.locator),
    connection: monitoringDrawerRawDimensionLabel(endpoint.connection),
    format: monitoringDrawerRawDimensionLabel(endpoint.format || firstValue(row, [`${direction}_connection_type`])),
  };
}

export function DataflowRouteEndpoint({
  direction,
  endpoint,
}: {
  direction: "source" | "destination";
  endpoint: { asset: string; connection: string; format: string };
}) {
  const stageClass = monitoringConnectionStageClass(endpoint.connection);
  return (
    <div className={["monitoring-dataflow-route-endpoint", `is-${direction}`, stageClass].filter(Boolean).join(" ")}>
      <span>{direction}</span>
      <strong>{endpoint.asset}</strong>
      <small>{endpoint.connection} · {endpoint.format}</small>
    </div>
  );
}

export function MaintenanceHealthChip({ health, reason }: { health: unknown; reason?: unknown }) {
  return (
    <Help className={`maintenance-table-health-chip ${maintenanceTableHealthClass(health)}`} title={String(reason ?? maintenanceTableHealthLabel(health))}>
      {maintenanceTableHealthLabel(health)}
    </Help>
  );
}

export type FreshnessDrawerHealthTone = "success" | "warning" | "failed" | "neutral";

export function freshnessHealthToneForSignals({
  hasEvidence,
  failed,
  needsReview,
  stale,
}: {
  hasEvidence: boolean;
  failed?: boolean;
  needsReview?: boolean;
  stale?: boolean;
}): FreshnessDrawerHealthTone {
  if (!hasEvidence) return "neutral";
  if (failed) return "failed";
  if (needsReview || stale) return "warning";
  return "success";
}

export function freshnessHealthFromCounts({
  observedDataflows,
  staleDataflows,
  latestRunIssues,
  latestWatermarkIssues,
  missingChecks,
  metricsKnown,
}: {
  observedDataflows: number;
  staleDataflows: number;
  latestRunIssues: number;
  latestWatermarkIssues: number;
  missingChecks: number;
  metricsKnown: boolean;
}): { label: string; intent: "good" | "warning" | "bad"; tone: FreshnessDrawerHealthTone } {
  if (!metricsKnown) return { label: "Evidence unknown", intent: "warning", tone: "warning" };
  if (observedDataflows === 0) return { label: "No execution evidence", intent: "bad", tone: "failed" };
  const tone = freshnessHealthToneForSignals({
    hasEvidence: true,
    needsReview: latestRunIssues > 0 || latestWatermarkIssues > 0 || missingChecks > 0,
    stale: staleDataflows > 0,
  });
  return tone === "success"
    ? { label: "Current", intent: "good", tone }
    : { label: "Needs review", intent: "warning", tone };
}

export function freshnessDrawerHealth(row: Record<string, unknown>): { label: string; tone: FreshnessDrawerHealthTone } {
  const hasEvidence = hasValue(row.latest_freshness_at) || hasValue(row.latest_run_at) || num(row, "run_count") > 0;
  if (!hasEvidence) return { label: "No evidence", tone: "neutral" };
  const latestStatus = String(row.latest_run_status ?? "unknown").trim().toLowerCase();
  const ageSeconds = freshnessAgeSeconds(row.age_seconds, row.age_days);
  const statusIssue = !["succeeded", "skipped"].includes(latestStatus);
  const issue = String(row.watermark_issue_state ?? "").trim().toLowerCase();
  const saveStatus = String(firstValue(row, ["save_status", "persistence_state"]) ?? "").trim().toLowerCase();
  const loadStatus = String(row.load_status ?? "").trim().toLowerCase();
  const failed = latestStatus === "failed" || latestStatus === "error"
    || issue === "error" || saveStatus === "save_error"
    || loadStatus === "load_error";
  const tone = freshnessHealthToneForSignals({
    hasEvidence,
    failed,
    needsReview: statusIssue || issue === "insufficient_evidence" || ageSeconds === null,
    stale: ageSeconds !== null && ageSeconds > 7 * 86_400,
  });
  if (tone === "failed") return { label: "Needs review", tone };
  if (tone === "warning") {
    return { label: ageSeconds !== null && ageSeconds > 7 * 86_400 ? "Stale" : "Needs review", tone };
  }
  return { label: "Current", tone };
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
    <Help
      className="freshness-run-stack-cell"
      title={[
        `Start: ${start}`,
        `End: ${end.slice(2)}`,
      ].join("\n")}
    >
      <strong>{start}</strong>
      <small><span>{end}</span></small>
    </Help>
  );
}

export function freshnessRunTimeLines(row: Record<string, unknown>, timezoneName?: string | null) {
  const start = formatTimestampForDisplay(row.start_time, timezoneName, "-");
  const end = formatTimestampForDisplay(row.end_time, timezoneName, "-");
  return [start, `→ ${end}`];
}

export function formatFreshnessAge(ageSeconds: unknown, ageDays: unknown) {
  const seconds = freshnessAgeSeconds(ageSeconds, ageDays);
  if (seconds !== null) {
    if (seconds < 60) return `${formatAgeNumber(Math.max(0, seconds))}s`;
    if (seconds < 3600) return `${formatAgeNumber(seconds / 60)}m`;
    if (seconds < 86400) return `${formatAgeNumber(seconds / 3600)}h`;
    return `${formatAgeNumber(seconds / 86400)}d`;
  }
  return "-";
}

export function freshnessAgeSeconds(ageSeconds: unknown, ageDays: unknown): number | null {
  const numberValue = (value: unknown) => {
    if (typeof value !== "number" && typeof value !== "string") return null;
    if (typeof value === "string" && !value.trim()) return null;
    const result = Number(value);
    return Number.isFinite(result) ? result : null;
  };
  const seconds = numberValue(ageSeconds);
  if (seconds !== null) return seconds;
  const days = numberValue(ageDays);
  return days === null ? null : days * 86_400;
}

export function DataflowPhaseContribution({ row }: { row: Record<string, unknown> }) {
  const segments = dataflowPhaseSegments(row);
  if (!segments.length) return <span className="monitor-muted">-</span>;
  const title = segments
    .map((segment) => `${phaseLabel(segment.phase)}: ${formatSeconds(segment.value)} (${formatPhasePercent(segment.percent)})`)
    .join("\n");
  return (
    <Help as="div" className="monitoring-phase-contribution" title={title}>
      <div className="monitoring-phase-stack" aria-label="Dataflow phase contribution">
        {segments.map((segment) => (
          <i key={segment.phase} className={`phase-${segment.phase}`} style={{ flex: `0 0 ${segment.percent}%` }} />
        ))}
      </div>
    </Help>
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
    <div className="monitoring-error-message-body monitoring-message-scroll">
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
  if (isLosslessJsonNumber(value)) return <Help title={formatTooltipNumber(value.value)}>{formatExactNumber(value.value)}</Help>;
  if (field?.endsWith("duration_seconds")) {
    const seconds = toApproximateNumber(value as MetricValue);
    return <Help title={formatTooltipNumber(value as MetricValue)}>{seconds === null ? "-" : formatSeconds(seconds)}</Help>;
  }
  if (field?.includes("bytes")) {
    const bytes = toApproximateNumber(value as MetricValue);
    return <Help title={formatTooltipBytes(value as MetricValue)}>{bytes === null ? "-" : formatBytes(bytes)}</Help>;
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value) || (value && typeof value === "object")) return <JsonBlock value={value} />;
  if (typeof value === "string" && looksJson(value)) return <JsonBlock value={value} />;
  if (typeof value === "string" && field && isMetricField(field) && isMetricInteger(value)) {
    const numeric = toApproximateNumber(value) ?? 0;
    return <Help title={formatTooltipNumber(value)}><SemanticValue value={semanticNumber(field, numeric)} /></Help>;
  }
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
  if (/^(?:unk|unknown)$/iu.test(rawValue)) return "unk";
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
  if (lowerField === "status" || lowerField.endsWith("_status")) {
    return { kind: "status", value };
  }
  if (lowerField.includes("reconciliation")) {
    return { kind: "text", value, intent: lowerValue === "matched" ? "success" : lowerValue === "mismatch" ? "failed" : "neutral" };
  }
  return { kind: "text", value };
}

export function SemanticValue({ value }: { value: SemanticValueModel }) {
  if (value.kind === "reconciliation") {
    const statusIntent = value.status.toLowerCase() === "matched" ? "success" : value.status.toLowerCase() === "mismatch" ? "failed" : "neutral";
    const countIntent = (toApproximateNumber(value.mismatch) ?? 0) > 0 ? "failed" : "neutral";
    return (
      <span className="monitoring-semantic-pair">
        <span className={`monitoring-semantic-value is-${statusIntent}`} style={semanticIntentStyle(statusIntent)}>{value.status}</span>
        <span aria-hidden="true">·</span>
        <span className={`monitoring-semantic-value is-${countIntent}`} style={semanticIntentStyle(countIntent)}><CompactNumberValue value={value.mismatch} /></span>
      </span>
    );
  }
  if (value.kind === "count") {
    const intent = value.intent ?? "neutral";
    return <span className={`monitoring-semantic-value is-${intent}`} style={semanticIntentStyle(intent)}><CompactNumberValue value={value.value} /></span>;
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
  const formatted = kind === "sql"
    ? formatPrettySql(String(value ?? ""))
    : kind === "list"
      ? formatListBlock(value)
      : formatCompactJsonBlock(value);
  return (
    <div className={`monitoring-code-box monitoring-code-box-${kind}`}>
      <Help as="button"
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
      </Help>
      <pre className={`monitoring-code-block${kind === "sql" ? " sql-syntax-light" : ""}`}>
        {kind === "sql" ? highlightSql(formatted) : highlightJson(formatted)}
      </pre>
    </div>
  );
}

export function JsonBlock({ value, compactArray = false }: { value: unknown; compactArray?: boolean }) {
  const [copied, setCopied] = useState(false);
  const formatted = formatJsonValue(value, compactArray);
  return (
    <div className="monitoring-json-box monitoring-inline-json-box">
      <Help as="button"
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
      </Help>
      <pre className="monitoring-inline-json monitoring-json-light">{highlightJson(formatted)}</pre>
    </div>
  );
}

export function safeJsonStringify(value: unknown) {
  try {
    return losslessStringify(value, null, 2) ?? String(value);
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

export function parseJsonLike(value: unknown) {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!looksJson(trimmed)) return value;
  return parseRawRecordJson(trimmed) ?? value;
}

export function formatCompactJsonObject(value: Record<string, unknown>) {
  const entries = Object.entries(value);
  if (!entries.length) return "{}";
  const lines: string[] = ["{"];
  entries.forEach(([key, item], index) => {
    const suffix = index + 1 < entries.length ? "," : "";
    lines.push(`  ${JSON.stringify(key)}: ${safeJsonStringify(item)}${suffix}`);
  });
  lines.push("}");
  return lines.join("\n");
}

export function formatCompactJsonArray(value: unknown) {
  let parsed = value;
  if (typeof value === "string") {
    parsed = parseRawRecordJson(value);
    if (parsed === null) return value;
  }
  if (!Array.isArray(parsed)) return safeJsonStringify(parsed);
  if (!parsed.length) return "[]";
  if (parsed.every(isPrimitiveJsonValue)) {
    return `[\n  ${parsed.map((item) => safeJsonStringify(item)).join(", ")}\n]`;
  }
  return safeJsonStringify(parsed);
}

export function isPrimitiveJsonValue(value: unknown) {
  return value === null || ["string", "number", "boolean"].includes(typeof value) || isLosslessJsonNumber(value);
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
  if (field.includes("bytes")) {
    const bytesValue = metricValue(row, field);
    const bytes = toApproximateNumber(bytesValue);
    return <Help title={formatTooltipBytes(bytesValue)}>{bytes === null ? "-" : formatBytes(bytes)}</Help>;
  }
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
  const message = effectiveMonitoringMessage(row);
  if (message === null) return <span className="monitor-muted">-</span>;
  const status = String(row.status || "").toLowerCase();
  return (
    <Help className={`monitoring-issue-cell${status === "failed" ? " is-error" : ""}`} title={message} aria-label={message}>
      {message}
    </Help>
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

function isMetricField(field: string) {
  return /(?:count|number|rows?|bytes?|files?|runs?|jobs?|records?|total|failed|succeeded|skipped|running|pending|difference|duration_seconds)$/iu.test(field);
}

export function formatJson(value: string) {
  return formatRawRecordJson(value);
}

export function humanize(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

/**
 * Keep identifiers readable in narrow drawer fields while preserving the
 * first and last token for values that use hyphen-delimited segments.
 */
export function compactDrawerIdentifier(value: unknown, maxLength = 18): string | null {
  if (value === null || value === undefined) return null;
  const text = String(value);
  if (text.length <= maxLength) return text;

  const groups = text.split("-").filter(Boolean);
  const separator = groups.length > 1 ? "-...-" : "...";
  const available = maxLength - separator.length;
  if (available <= 0) return text.slice(0, maxLength);

  const headLength = Math.ceil(available / 2);
  const tailLength = Math.floor(available / 2);
  const headSource = groups.length > 1 ? groups[0] : text;
  const tailSource = groups.length > 1 ? groups.at(-1) ?? "" : text;
  return `${headSource.slice(0, headLength)}${separator}${tailLength > 0 ? tailSource.slice(-tailLength) : ""}`;
}
