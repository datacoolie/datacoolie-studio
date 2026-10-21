import type { MonitoringDetailKind } from "./MonitoringDetailDrawer";
import type { TableSort } from "../../shared/components/DataTable";
import type { MonitoringDataflowRunDetailEnvelope, MonitoringRegistryOrigin, MonitoringRegistryRunReference } from "./monitoringQueries";
import { monitoringIdentityVersion, monitoringRowSessionScope, sameMonitoringRegistryInstant } from "./monitoringQueries";

export interface MonitoringDetailTarget {
  kind: MonitoringDetailKind;
  row: Record<string, unknown>;
}

export interface MonitoringDetailEvidencePage {
  limit: number;
  offset: number;
  sort: TableSort;
}

export type { MonitoringRegistryRunReference } from "./monitoringQueries";

/** Extract the physical winner reference attached to a registry run row. */
export function monitoringRegistryRunReference(
  row: Record<string, unknown>,
  analyticsGenerationOrOrigin?: string | null | MonitoringRegistryOrigin,
  origin?: MonitoringRegistryOrigin | null,
): MonitoringRegistryRunReference | null {
  const entityId = firstString(row, ["dataflow_id"]);
  const runId = firstString(row, ["dataflow_run_id", "latest_run_id"]);
  const sourceId = toNullableNumber(firstPresentValue(row, ["_source_id", "latest_source_id"]));
  if (!entityId || !runId || sourceId === null) return null;

  const hasSession = hasAny(row, ["log_session_id", "latest_log_session_id"]);
  const sessionValue = firstPresentValue(row, ["log_session_id", "latest_log_session_id"]);
  const logSessionScope = hasSession
    ? sessionValue === null ? "null" : "exact"
    : "all";
  const resolvedOrigin = isMonitoringRegistryOrigin(analyticsGenerationOrOrigin)
    ? analyticsGenerationOrOrigin
    : origin ?? null;
  const analyticsGeneration = resolvedOrigin?.analyticsGeneration
    ?? (typeof analyticsGenerationOrOrigin === "string" ? analyticsGenerationOrOrigin : null);
  if (!analyticsGeneration) return null;
  const fileUri = firstPresentValue(row, ["_file_uri", "latest_file_uri"]);
  const recordLine = firstPresentValue(row, ["_record_line", "latest_record_line"]);
  return {
    dataflow_id: entityId,
    dataflow_run_id: runId,
    _source_id: sourceId,
    ...(logSessionScope === "exact" ? { log_session_id: String(sessionValue ?? "") } : logSessionScope === "null" ? { log_session_id: null } : {}),
    ...(fileUri !== undefined ? { _file_uri: fileUri == null ? null : String(fileUri) } : {}),
    ...(recordLine !== undefined ? { _record_line: toNullableNumber(recordLine) } : {}),
    logSessionScope,
    analyticsGeneration,
    ...(resolvedOrigin ? { registryOrigin: resolvedOrigin } : {}),
  };
}

/**
 * Validate an exact-run response before it is merged into a registry row.
 * Missing generation is intentionally invalid for registry-origin callers.
 */
export function validateMonitoringRegistryRunEvidence(
  reference: MonitoringRegistryRunReference,
  value: MonitoringDataflowRunDetailEnvelope | Record<string, unknown> | null | undefined,
) {
  const envelope = isRunDetailEnvelope(value) ? value : null;
  const row = envelope?.row ?? (value && isRecord(value) ? value : null);
  if (!row) return { ok: false as const, reason: "run_not_found" };
  const responseGeneration = envelope?.summary?.analytics_generation;
  if (!responseGeneration || !reference.analyticsGeneration || responseGeneration !== reference.analyticsGeneration) {
    return { ok: false as const, reason: "analytics_generation_mismatch" };
  }
  const expectedIdentityVersion = monitoringIdentityVersion(reference.registryOrigin?.identityVersion);
  if (expectedIdentityVersion && monitoringIdentityVersion(envelope?.summary?.identity_version) !== expectedIdentityVersion) {
    return { ok: false as const, reason: "identity_version_mismatch" };
  }
  const origin = reference.registryOrigin;
  if (origin?.asOf && envelope?.summary?.as_of && !sameMonitoringRegistryInstant(origin.asOf, envelope.summary.as_of)) {
    return { ok: false as const, reason: "as_of_mismatch" };
  }
  if (String(row.dataflow_id ?? "") !== reference.dataflow_id) return { ok: false as const, reason: "entity_mismatch" };
  if (String(row.dataflow_run_id ?? "") !== reference.dataflow_run_id) return { ok: false as const, reason: "run_mismatch" };

  if (reference._source_id !== undefined) {
    const responseSourceId = row._source_id;
    if (toNullableNumber(responseSourceId) !== reference._source_id) return { ok: false as const, reason: "source_mismatch" };
  }
  if (reference.logSessionScope === "null") {
    if (!Object.prototype.hasOwnProperty.call(row, "log_session_id") || row.log_session_id !== null) {
      return { ok: false as const, reason: "session_mismatch" };
    }
  } else if ((reference.logSessionScope ?? (reference.log_session_id === null ? "null" : reference.log_session_id === undefined ? "all" : "exact")) === "exact" && String(row.log_session_id ?? "") !== String(reference.log_session_id ?? "")) {
    return { ok: false as const, reason: "session_mismatch" };
  }
  if (reference._file_uri !== undefined && String(row._file_uri ?? "") !== String(reference._file_uri ?? "")) {
    return { ok: false as const, reason: "file_mismatch" };
  }
  if (reference._record_line !== undefined && toNullableNumber(row._record_line) !== reference._record_line) {
    return { ok: false as const, reason: "line_mismatch" };
  }
  return { ok: true as const, row, summary: envelope?.summary };
}

export function sameMonitoringJobScope(row: Record<string, unknown>, candidate: Record<string, unknown>) {
  const jobId = exactIdentityValue(row, "job_id");
  const candidateJobId = exactIdentityValue(candidate, "job_id");
  if (jobId === undefined || candidateJobId === undefined || String(jobId) !== String(candidateJobId)) return false;
  const sessionScope = monitoringRowSessionScope(row);
  if (sessionScope !== "all") {
    if (monitoringRowSessionScope(candidate) !== sessionScope) return false;
    if (sessionScope === "exact" && String(row.log_session_id ?? "") !== String(candidate.log_session_id ?? "")) return false;
  }
  const sourceId = row._source_id ?? row.source_id;
  const candidateSourceId = candidate._source_id ?? candidate.source_id;
  if (sourceId == null) return true;
  if (candidateSourceId == null) return false;
  return String(sourceId) === String(candidateSourceId);
}

export function monitoringFailureDetailKind(row: Record<string, unknown>): MonitoringDetailKind {
  const hasDataflowRunId = row.dataflow_run_id !== undefined && row.dataflow_run_id !== null;
  const hasDataflowId = row.dataflow_id !== undefined && row.dataflow_id !== null;
  return row.failure_kind === "dataflow" || hasDataflowRunId || hasDataflowId ? "dataflow" : "failure";
}

function exactIdentityValue(row: Record<string, unknown>, field: string) {
  if (!Object.prototype.hasOwnProperty.call(row, field) || row[field] === undefined || row[field] === null) return undefined;
  return row[field];
}

export function monitoringDetailEvidenceRequest(
  detail: MonitoringDetailTarget | null,
  baseParams: Record<string, string | number | undefined>,
  page: MonitoringDetailEvidencePage,
): { key: string; params: Record<string, string | number | undefined> } | null {
  if (!detail || !isPagedEvidenceKind(detail.kind)) return null;
  const investigateKind = detail.kind === "job"
    ? "job_id"
      : detail.kind === "maintenance"
      ? "physical_destination"
      : "dataflow_id";
  const investigateValue = detailEvidenceValue(detail);
  if (investigateValue === undefined) return null;
  const sessionScope = detail.kind === "job"
    ? monitoringRowSessionScope(detail.row)
    : normalizeBaseSessionScope(baseParams.log_session_scope);
  const sessionId = sessionScope === "exact"
    ? detail.kind === "job"
      ? String(detail.row.log_session_id ?? "")
      : String(baseParams.log_session_id ?? "")
    : undefined;
  const sourceId = detail.row._source_id ?? detail.row.source_id;
  const identityVersion = firstString(detail.row, ["identity_version"]);
  const scopedBaseParams = detail.kind === "freshness"
    && (!baseParams.operationType || baseParams.operationType === "all")
    ? { ...baseParams, operationType: "etl" }
    : baseParams;
  return {
    params: {
      ...scopedBaseParams,
      ...(detail.kind === "maintenance" ? { view: "destination-runs" } : {}),
      ...(detail.kind === "maintenance" && identityVersion ? { expectedIdentityVersion: identityVersion } : {}),
      limit: page.limit,
      offset: page.offset,
      sortBy: page.sort.sortBy,
      sortDir: page.sort.sortDir,
      investigateKind: detail.kind === "maintenance" ? baseParams.investigateKind : investigateKind,
      investigateValue: detail.kind === "maintenance" ? baseParams.investigateValue : investigateValue,
      ...(detail.kind === "maintenance" ? { destinationTarget: investigateValue } : {}),
      ...(sourceId == null || detail.kind === "maintenance" ? {} : { investigateSourceId: Number(sourceId) }),
      log_session_scope: sessionScope,
      ...(sessionScope === "exact" ? { log_session_id: sessionId } : {}),
    },
    key: `${detail.kind}:${investigateValue}:${sessionScope}:${sessionId ?? ""}:${sourceId == null ? "all" : String(sourceId)}${identityVersion ? `:${identityVersion}` : ""}`,
  };
}

export function isPagedEvidenceKind(kind: MonitoringDetailKind) {
  return kind === "job" || kind === "freshness" || kind === "maintenance" || kind === "volume";
}

export function mergeDataflowRunDetail(
  summaryRow: Record<string, unknown>,
  canonicalRow: Record<string, unknown> | null | undefined,
) {
  return canonicalRow ? { ...summaryRow, ...canonicalRow } : summaryRow;
}

function isRunDetailEnvelope(value: unknown): value is MonitoringDataflowRunDetailEnvelope {
  return isRecord(value) && Object.prototype.hasOwnProperty.call(value, "row") && isRecord(value.summary);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isMonitoringRegistryOrigin(value: unknown): value is MonitoringRegistryOrigin {
  return value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && "environmentId" in value
    && "dataflowId" in value
    && "analyticsGeneration" in value;
}

function hasAny(row: Record<string, unknown>, keys: string[]) {
  return keys.some((key) => Object.prototype.hasOwnProperty.call(row, key));
}

function firstValue(row: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(row, key)) return row[key];
  }
  return undefined;
}

function firstPresentValue(row: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(row, key)) return row[key];
  }
  return undefined;
}

function firstString(row: Record<string, unknown>, keys: string[]) {
  const value = firstValue(row, keys);
  return value === undefined || value === null || String(value).trim() === "" ? undefined : String(value);
}

function toNullableNumber(value: unknown): number | null {
  if (value === undefined || value === null || value === "") return null;
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}

function detailEvidenceValue(detail: MonitoringDetailTarget) {
  if (detail.kind === "job") return exactFieldValue(detail.row, "job_id");
  if (detail.kind === "maintenance") {
    return exactFieldValue(detail.row, "target");
  }
  return exactFieldValue(detail.row, "dataflow_id");
}

function exactFieldValue(row: Record<string, unknown>, field: string) {
  if (!Object.prototype.hasOwnProperty.call(row, field) || row[field] === undefined || row[field] === null) return undefined;
  return String(row[field]);
}

function normalizeBaseSessionScope(value: unknown): "all" | "null" | "exact" {
  return value === "null" || value === "exact" ? value : "all";
}
