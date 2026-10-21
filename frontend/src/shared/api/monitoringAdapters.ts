import type {
  MonitoringDataflowRecordsResponseTransport,
  MonitoringDataflowRegistryResponseTransport,
  MonitoringDiagnosticsEvidenceResponseTransport,
  MonitoringEvidenceResponseTransport,
  MonitoringFilterOptionsResponseTransport,
  MonitoringJobRecordsResponseTransport,
  MonitoringLatestStatusResponseTransport,
  MonitoringSystemLogsResponseTransport,
} from "./contractTypes";
import type {
  DiagnosticsEvidenceResponse,
  JobRecord,
  LatestStatusResponse,
  MonitoringFilterOptionsResponse,
  MonitoringRecord,
  MonitoringRecordsResponse,
  MonitoringRegistryRecord,
  MonitoringRegistryResponse,
  SystemLogResponse,
} from "./domainTypes";
import { isLosslessNumber, parse, stringify } from "lossless-json";

export type LosslessJsonValue =
  | null
  | boolean
  | string
  | number
  | bigint
  | { readonly isLosslessNumber: true; readonly value: string }
  | LosslessJsonValue[]
  | { [key: string]: LosslessJsonValue };

/** Parse API evidence without allowing large integers or decimal tokens to round. */
export function parseRawRecordJson(value: unknown): LosslessJsonValue | null {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    return parse(value) as LosslessJsonValue;
  } catch {
    return null;
  }
}

/** Format reconstructed native execution evidence without adding client rounding. */
export function formatRawRecordJson(value: unknown): string {
  if (typeof value !== "string") return "";
  const parsed = parseRawRecordJson(value);
  if (parsed === null) return value;
  try {
    return stringify(parsed, null, 2) ?? value;
  } catch {
    return value;
  }
}

/** Return API JSON unchanged for copy; execution rows are bounded reconstructed evidence and System Logs stay source-backed. */
export function rawRecordJsonForCopy(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

export function monitoringRawRecord(value: unknown): Record<string, unknown> {
  const row = toRecord(value);
  const parsed = parseRawRecordJson(row.raw_record_json ?? row.latest_raw_record_json);
  return isRecord(parsed) ? parsed : row;
}

export function monitoringAttempts(value: unknown): LosslessJsonValue[] {
  const row = monitoringRawRecord(value);
  return Array.isArray(row.attempts) ? row.attempts as LosslessJsonValue[] : [];
}

export function monitoringDiagnosticIssues(value: unknown): LosslessJsonValue[] {
  const row = monitoringRawRecord(value);
  return Array.isArray(row.diagnostic_issues) ? row.diagnostic_issues as LosslessJsonValue[] : [];
}

export function isLosslessJsonNumber(value: unknown): value is { readonly isLosslessNumber: true; readonly value: string } {
  return isLosslessNumber(value);
}

export function toMonitoringDataflowRecords(
  response: MonitoringDataflowRecordsResponseTransport,
): MonitoringRecordsResponse<MonitoringRecord> {
  return toRecordsResponse(response, response.records?.map(normalizeMonitoringRecord) as MonitoringRecord[] | undefined);
}

export function toMonitoringJobRecords(
  response: MonitoringJobRecordsResponseTransport,
): MonitoringRecordsResponse<JobRecord> {
  return toRecordsResponse(response, response.records?.map(normalizeMonitoringRecord) as JobRecord[] | undefined);
}

export function toMonitoringEvidence(
  response: MonitoringEvidenceResponseTransport,
): MonitoringRecordsResponse<Record<string, unknown>> {
  return toRecordsResponse(response, response.records?.map(normalizeMonitoringRecord));
}

export function toMonitoringDiagnosticsEvidence(
  response: MonitoringDiagnosticsEvidenceResponseTransport,
): DiagnosticsEvidenceResponse {
  return {
    mode: response.mode,
    group: (response.group ?? null) as DiagnosticsEvidenceResponse["group"],
    groups: (response.groups ?? []) as DiagnosticsEvidenceResponse["groups"],
    objects: (response.objects ?? []) as DiagnosticsEvidenceResponse["objects"],
    errors: response.errors ?? [],
    summary: toRecordsResponse(response, []).summary,
  };
}

export function toMonitoringDataflowRegistry(
  response: MonitoringDataflowRegistryResponseTransport,
): MonitoringRegistryResponse {
  return {
    ...toRecordsResponse(response, (response.records ?? []).map(normalizeMonitoringRecord) as MonitoringRegistryRecord[]),
    section: response.section,
  };
}

export function toMonitoringFilterOptions(
  response: MonitoringFilterOptionsResponseTransport,
): MonitoringFilterOptionsResponse {
  return {
    options: Object.fromEntries(
      Object.entries(response.options ?? {}).map(([field, values]) => [
        field,
        values.map(({ value, label, count }) => ({
          value,
          label,
          ...(count == null ? {} : { count }),
        })),
      ]),
    ),
    summary: response.summary,
    errors: response.errors ?? [],
  };
}

export function toLatestStatus(
  response: MonitoringLatestStatusResponseTransport,
): LatestStatusResponse {
  return {
    latest_by_id: (response.latest_by_id ?? {}) as Record<string, MonitoringRecord>,
    latest_by_name: (response.latest_by_name ?? {}) as Record<string, MonitoringRecord>,
    ambiguous_names: response.ambiguous_names ?? [],
    errors: response.errors ?? [],
  };
}

export function toMonitoringSystemLogs(
  response: MonitoringSystemLogsResponseTransport,
): SystemLogResponse {
  return {
    records: response.records?.map(normalizeMonitoringRecord) ?? [],
    total: response.total,
    files: response.files ?? [],
    errors: response.errors ?? [],
  };
}

function normalizeMonitoringRecord(value: unknown): Record<string, unknown> {
  const record = toRecord(value);
  // Keep evidence JSON as a string. Parsing is intentionally lazy so the
  // reconstructed text remains available for copy and unknown fields survive.
  return { ...record };
}

function toRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function toRecordsResponse<T extends Record<string, unknown>>(
  response: {
    errors?: Record<string, unknown>[];
    summary: {
      records: number;
      total_records: number;
      limit: number;
      offset: number;
      cache?: string | null;
      analytics_generation?: string | null;
      identity_version?: string | null;
      as_of?: string | null;
    };
  },
  records: T[] | undefined,
): MonitoringRecordsResponse<T> {
  return {
    records: records ?? [],
    errors: response.errors ?? [],
    summary: {
      records: response.summary.records,
      total_records: response.summary.total_records,
      limit: response.summary.limit,
      offset: response.summary.offset,
      ...(response.summary.cache == null ? {} : { cache: response.summary.cache }),
      ...(response.summary.analytics_generation == null ? {} : { analytics_generation: response.summary.analytics_generation }),
      ...(response.summary.identity_version == null ? {} : { identity_version: response.summary.identity_version }),
      ...(response.summary.as_of == null ? {} : { as_of: response.summary.as_of }),
    },
  };
}
