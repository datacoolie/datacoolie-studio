import { formatTimestampForLogDisplay, parseTimestamp } from "../../shared/time";
import { formatRawRecordJson, isLosslessJsonNumber, monitoringRawRecord, rawRecordJsonForCopy } from "../../shared/api/monitoringAdapters";
import { stringify as losslessStringify } from "lossless-json";

export interface SystemLogPage {
  records: Array<Record<string, unknown>>;
  total: number;
  files: unknown[];
  errors: Array<Record<string, unknown>>;
}

export interface SystemLogParts {
  time: string;
  level: string;
  logger: string;
  func: string;
  line: string;
  schemaVersion: string | null;
  datacoolieVersion: string | null;
  logSessionId: string | null;
  manifestSessionId: string | null;
  jobId: string | null;
  jobNum: string | null;
  jobIndex: string | null;
  module: string | null;
  dataflowId: string | null;
  dataflowRunId: string | null;
  eventName: string | null;
  attemptNumber: string | null;
  diagnosticIssues: unknown;
  message: string;
  excInfo: string | null;
}

export function systemLogParts(record: Record<string, unknown>, timezoneName?: string | null): SystemLogParts {
  const rawRecord = monitoringRawRecord(record);
  const timestamp = firstPresent(record, rawRecord, ["ts", "timestamp"]);
  const level = firstPresent(record, rawRecord, ["level"]);
  const logger = firstPresent(record, rawRecord, ["logger"]);
  const func = firstPresent(record, rawRecord, ["func", "function"]);
  const schemaVersion = firstPresent(record, rawRecord, ["log_schema_version", "schema_version"]);
  const datacoolieVersion = firstPresent(record, rawRecord, ["datacoolie_version", "version"]);
  const logSessionId = firstPresent(record, rawRecord, ["log_session_id", "session_id"]);
  const manifestSessionId = firstPresent(record, rawRecord, ["_log_session_id"]);
  const dataflowId = firstPresent(record, rawRecord, ["dataflow_id"]);
  const dataflowRunId = firstPresent(record, rawRecord, ["dataflow_run_id"]);
  const eventName = firstPresent(record, rawRecord, ["event_name"]);
  const message = firstPresent(record, rawRecord, ["msg", "message"]);
  const excInfo = firstPresent(record, rawRecord, ["exc_info"]);
  return {
    time: displaySystemLogValue(formatTimestampForLogDisplay(timestamp, timezoneName)),
    level: valueOr(level, "UNKNOWN").toUpperCase(),
    logger: valueOr(logger, "-"),
    func: valueOr(func, "-"),
    line: valueOr(firstPresent(record, rawRecord, ["line"]), "-"),
    schemaVersion: optionalValue(schemaVersion),
    datacoolieVersion: optionalValue(datacoolieVersion),
    logSessionId: optionalValue(logSessionId),
    manifestSessionId: optionalValue(manifestSessionId),
    jobId: optionalValue(firstPresent(record, rawRecord, ["job_id"])),
    jobNum: optionalValue(firstPresent(record, rawRecord, ["job_num", "job_number"])),
    jobIndex: optionalValue(firstPresent(record, rawRecord, ["job_index"])),
    module: optionalValue(firstPresent(record, rawRecord, ["module", "module_name"])),
    dataflowId: optionalValue(dataflowId),
    dataflowRunId: optionalValue(dataflowRunId),
    eventName: optionalValue(eventName),
    attemptNumber: optionalValue(firstPresent(record, rawRecord, ["attempt_number"])),
    diagnosticIssues: firstPresent(record, rawRecord, ["diagnostic_issues"]),
    message: valueOr(message, "-"),
    excInfo: optionalValue(excInfo),
  };
}

export function formatSystemLogRecord(record: Record<string, unknown>, timezoneName?: string | null) {
  const parts = systemLogParts(record, timezoneName);
  const segments = [
    parts.time,
    `[${displaySystemLogValue(parts.level)}]`,
    displaySystemLogValue(parts.logger),
    parts.dataflowId ? `[${displaySystemLogValue(parts.dataflowId)}]` : null,
    parts.eventName ? `[${displaySystemLogValue(parts.eventName)}]` : null,
  ].filter((segment): segment is string => Boolean(segment));
  const formatted = `${segments.join(" ")} - ${displaySystemLogMessage(parts.message)}`;
  return parts.excInfo ? `${formatted}\n${parts.excInfo}` : formatted;
}

export function displaySystemLogValue(value: string) {
  return value.replace(/[\u0000-\u001f\u007f-\u009f]/g, formatControlCharacter);
}

export function displaySystemLogMessage(value: string) {
  return value.replace(/[\u0000-\u0009\u000b\u000c\u000e-\u001f\u007f-\u009f]/g, formatControlCharacter);
}

export function formatFullSystemLogRecord(record: Record<string, unknown>, timezoneName?: string | null) {
  const raw = rawRecordJsonForCopy(record.raw_record_json);
  return `${formatSystemLogRecord(record, timezoneName)}\n${raw ? formatRawRecordJson(raw) : JSON.stringify(record, null, 2) ?? String(record)}`;
}

export function logLevelTone(level: string) {
  const normalized = level.trim().toLowerCase();
  if (normalized === "fatal" || normalized === "critical") return "critical";
  if (normalized === "error" || normalized === "exception") return "error";
  if (normalized === "warning" || normalized === "warn") return "warning";
  if (normalized === "info") return "info";
  if (normalized === "debug" || normalized === "trace") return "debug";
  return "unknown";
}

export function uniqueSystemLogFiles(files: unknown[]) {
  const seen = new Set<string>();
  const unique: Array<Record<string, unknown>> = [];
  for (const value of files) {
    const file = toRecord(value);
    const source = firstPresent(file, ["source_id"]);
    const uri = firstPresent(file, ["file_uri"]);
    const key = `${valueOr(source, "")}\u0000${valueOr(uri, "")}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(file);
  }
  return unique;
}

export function collectSystemLogReadErrors(pages: Array<{ errors?: Array<Record<string, unknown>> }>) {
  const seenOnEarlierPage = new Set<string>();
  const errors: Array<Record<string, unknown>> = [];
  for (const page of pages) {
    const pageKeys = new Set<string>();
    for (const error of page.errors ?? []) {
      const key = stableRecordFingerprint(error);
      if (!seenOnEarlierPage.has(key)) errors.push(error);
      pageKeys.add(key);
    }
    for (const key of pageKeys) seenOnEarlierPage.add(key);
  }
  return errors;
}

export function systemLogDetailEntries(record: Record<string, unknown>, parts: SystemLogParts) {
  const entries: Array<{ label: string; value: unknown; copyable?: boolean }> = [];
  const add = (label: string, value: unknown, copyable = false) => {
    if (value !== undefined) entries.push({ label, value, copyable });
  };
  const rawRecord = monitoringRawRecord(record);
  add("Timestamp", firstPresent(record, rawRecord, ["ts", "timestamp"]), true);
  add("Level", firstPresent(record, rawRecord, ["level"]));
  add("Logger", parts.logger);
  add("Module", parts.module);
  add("Function", parts.func);
  add("Line", parts.line);
  add("Dataflow ID", parts.dataflowId, true);
  add("LogEvent", parts.eventName, true);
  add("Attempt number", parts.attemptNumber);
  add("Diagnostic issues", parts.diagnosticIssues);
  add("Dataflow run ID", parts.dataflowRunId, true);
  add("Job ID", parts.jobId, true);
  add("Session ID", parts.logSessionId, true);
  add("Job number", parts.jobNum);
  add("Job index", parts.jobIndex);
  add("Schema version", parts.schemaVersion);
  add("DataCoolie version", parts.datacoolieVersion);
  add("Manifest session", parts.manifestSessionId, true);
  add("Source ID", firstPresent(record, rawRecord, ["_source_id", "source_id"]), true);
  add("File URI", firstPresent(record, rawRecord, ["_file_uri", "file_uri", "uri"]), true);
  add("File kind", firstPresent(record, rawRecord, ["_file_kind", "file_kind"]));
  add("Message (msg)", firstPresent(record, rawRecord, ["msg"]));
  add("Traceback (exc_info)", firstPresent(record, rawRecord, ["exc_info"]));
  return entries;
}

export function sortLogRecords(records: Array<Record<string, unknown>>) {
  return records.map((record, index) => {
    const rawRecord = monitoringRawRecord(record);
    return { record, index, timestamp: parseTimestamp(valueOr(firstPresent(record, rawRecord, ["ts", "timestamp"]), "")) };
  })
    .sort((left, right) => {
      if (left.timestamp === null && right.timestamp === null) return left.index - right.index;
      if (left.timestamp === null) return 1;
      if (right.timestamp === null) return -1;
      return left.timestamp - right.timestamp || left.index - right.index;
    })
    .map(({ record }) => record);
}

export function logRecordKey(record: Record<string, unknown>, index: number) {
  const rawRecord = monitoringRawRecord(record);
  return [
    firstPresent(record, rawRecord, ["ts", "timestamp"]),
    firstPresent(record, rawRecord, ["level"]),
    firstPresent(record, rawRecord, ["logger"]),
    firstPresent(record, rawRecord, ["dataflow_id"]),
    firstPresent(record, rawRecord, ["event_name"]),
    index,
  ].map(String).join(":");
}

export function formatRecordValue(value: unknown) {
  if (value === null) return "null";
  if (typeof value === "string") return value;
  if (isLosslessJsonNumber(value)) return value.value;
  try {
    return losslessStringify(value) ?? String(value);
  } catch {
    return String(value);
  }
}

export function readErrorSummary(error: Record<string, unknown>) {
  const source = firstPresent(error, ["source_id"]);
  const uri = firstPresent(error, ["uri"]);
  const code = firstPresent(error, ["code"]);
  const message = firstPresent(error, ["message"]);
  const parts = [
    source !== undefined ? `Source ${formatRecordValue(source)}` : null,
    uri !== undefined ? `URI ${formatRecordValue(uri)}` : null,
    code !== undefined ? `Code ${formatRecordValue(code)}` : null,
    message !== undefined ? formatRecordValue(message) : null,
  ].filter((part): part is string => Boolean(part));
  return parts.join("; ") || "Log file read warning";
}

function firstPresent(
  record: Record<string, unknown>,
  rawRecordOrKeys: Record<string, unknown> | string[],
  maybeKeys?: string[],
) {
  const rawRecord = Array.isArray(rawRecordOrKeys) ? {} : rawRecordOrKeys;
  const keys = Array.isArray(rawRecordOrKeys) ? rawRecordOrKeys : maybeKeys ?? [];
  for (const key of keys) {
    if (hasOwn(record, key)) return record[key];
    if (hasOwn(rawRecord, key)) return rawRecord[key];
  }
  return undefined;
}

function formatControlCharacter(character: string) {
  const code = character.charCodeAt(0).toString(16).padStart(2, "0");
  return `\\u00${code}`;
}

function valueOr(value: unknown, fallback: string) {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function optionalValue(value: unknown) {
  if (value === null || value === undefined) return null;
  const text = String(value);
  return text.trim() ? text : null;
}

function hasOwn(record: Record<string, unknown>, field: string) {
  return Object.prototype.hasOwnProperty.call(record, field);
}

function toRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function stableRecordFingerprint(value: Record<string, unknown>) {
  return JSON.stringify(stableValue(value));
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue);
  if (!value || typeof value !== "object") return value;
  const record = value as Record<string, unknown>;
  return Object.keys(record).sort().reduce<Record<string, unknown>>((result, key) => {
    result[key] = stableValue(record[key]);
    return result;
  }, {});
}
