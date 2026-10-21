import { describe, expect, it } from "vitest";
import {
  collectSystemLogReadErrors,
  displaySystemLogValue,
  displaySystemLogMessage,
  formatFullSystemLogRecord,
  formatSystemLogRecord,
  logLevelTone,
  systemLogDetailEntries,
  systemLogParts,
  systemLogScopeParams,
  uniqueSystemLogFiles,
} from "./SystemLogViewer";

describe("SystemLogViewer formatting", () => {
  it("formats the readable primary line with milliseconds and only the primary identity fields", () => {
    const record = {
      ts: "2026-07-14T14:19:04+07:00",
      level: "info",
      logger: "DataCoolie.driver",
      func: "load_dataflows",
      line: 270,
      dataflow_id: "df-orders",
      event_name: "dataflow.started",
      msg: "Loading dataflows",
      job_id: "job-1",
      log_session_id: "session-1",
    };

    expect(formatSystemLogRecord(record, "Asia/Saigon")).toBe(
      "2026-07-14 14:19:04.000 GMT+7 [INFO] DataCoolie.driver [df-orders] [dataflow.started] - Loading dataflows",
    );
    expect(formatSystemLogRecord(record)).not.toContain("load_dataflows:270");
    expect(formatSystemLogRecord(record)).not.toContain("job-1");
    expect(systemLogParts(record, "Asia/Saigon").dataflowId).toBe("df-orders");
  });

  it("omits absent identity segments and keeps the exact event and IDs", () => {
    const record = {
      ts: "2026-07-14T14:19:04.988Z",
      level: "INFO",
      logger: "driver",
      dataflow_id: "`Customers`/BRONZE",
      event_name: "LogEvent/Quoted",
      dataflow_run_id: "run/custom#skip",
      msg: "Started",
    };

    expect(formatSystemLogRecord(record, "UTC")).toBe(
      "2026-07-14 14:19:04.988 UTC [INFO] driver [`Customers`/BRONZE] [LogEvent/Quoted] - Started",
    );
    expect(formatSystemLogRecord({ ts: "2026-07-14", level: "INFO", logger: "driver", msg: "Ready" })).toBe(
      "2026-07-14 [INFO] driver - Ready",
    );
    expect(systemLogParts(record).dataflowRunId).toBe("run/custom#skip");
  });

  it("escapes control characters for visible values while preserving raw evidence", () => {
    const record = {
      ts: "2026-07-14T14:19:04.001Z",
      level: "INFO\nALERT",
      logger: "driver\tworker",
      dataflow_id: "DF\u0000A",
      event_name: "event\nname",
      msg: "line one\nline two",
    };

    expect(displaySystemLogValue(record.logger)).toBe("driver\\u0009worker");
    expect(formatSystemLogRecord(record, "UTC")).toContain("[INFO\\u000aALERT] driver\\u0009worker [DF\\u0000A] [event\\u000aname]");
    expect(formatFullSystemLogRecord(record)).toContain('"dataflow_id": "DF\\u0000A"');
  });

  it("escapes C1 and malformed timestamp controls while preserving message line breaks", () => {
    expect(displaySystemLogValue("before\u0080after\u009f")).toBe("before\\u0080after\\u009f");
    expect(displaySystemLogMessage("line one\r\nline two\tcolumn\u0085next")).toBe("line one\r\nline two\\u0009column\\u0085next");
    expect(formatSystemLogRecord({ ts: "malformed\nstamp", msg: "Ready" })).toContain("malformed\\u000astamp");
  });

  it("keeps manifest session provenance separate from the canonical session", () => {
    const record = { _log_session_id: "manifest-session", msg: "Ready" };
    const parts = systemLogParts(record);
    expect(parts.logSessionId).toBeNull();
    expect(parts.manifestSessionId).toBe("manifest-session");
    expect(systemLogDetailEntries(record, parts)).toContainEqual({
      label: "Manifest session",
      value: "manifest-session",
      copyable: true,
    });
  });

  it("uses canonical msg presence over the historical message alias", () => {
    expect(systemLogParts({ msg: "current", message: "stale" }).message).toBe("current");
    expect(systemLogParts({ msg: "", message: "stale" }).message).toBe("-");
    expect(systemLogParts({ msg: null, message: "stale" }).message).toBe("-");
    expect(systemLogParts({ message: "legacy" }).message).toBe("legacy");
  });

  it("keeps supplied technical context, traceback, and unknown fields in full evidence", () => {
    const record = {
      log_schema_version: 4,
      datacoolie_version: "0.2.0",
      log_session_id: "Session-A/BRONZE",
      job_id: "ABCDEFAB-0000-0000-0000-000000000001",
      job_num: 7,
      job_index: 0,
      module: "DataCoolie.Driver",
      func: "load_dataflows",
      line: 270,
      dataflow_id: "`Customers`/BRONZE",
      dataflow_run_id: "run/custom#skip",
      event_name: "dataflow.failed",
      level: "ERROR",
      msg: "Load failed",
      message: "stale alias",
      exc_info: "Traceback\nValueError: bad input",
      provider_unknown: { preserved: true },
    };
    const parts = systemLogParts(record);

    expect(parts.schemaVersion).toBe("4");
    expect(parts.datacoolieVersion).toBe("0.2.0");
    expect(parts.logSessionId).toBe("Session-A/BRONZE");
    expect(parts.jobId).toBe("ABCDEFAB-0000-0000-0000-000000000001");
    expect(parts.jobNum).toBe("7");
    expect(parts.jobIndex).toBe("0");
    expect(parts.module).toBe("DataCoolie.Driver");
    expect(parts.message).toBe("Load failed");
    expect(formatSystemLogRecord(record)).toContain("Load failed\nTraceback\nValueError: bad input");
    const full = formatFullSystemLogRecord(record);
    expect(full).toContain('"provider_unknown": {');
    expect(full).toContain('"preserved": true');
    expect(full).toContain("Session-A/BRONZE");
  });

  it("reads attempt and diagnostic markers from lossless raw records", () => {
    const raw = '{"ts":"2026-07-14T14:19:04Z","level":"WARNING","msg":"attempt detail","attempt_number":2,"diagnostic_issues":[{"code":"omitted_detail"}],"large":9007199254740993}';
    const record = { raw_record_json: raw };
    const parts = systemLogParts(record);
    expect(parts.attemptNumber).toBe("2");
    expect(parts.diagnosticIssues).toEqual([{ code: "omitted_detail" }]);
    expect(systemLogDetailEntries(record, parts).find((entry) => entry.label === "Attempt number")?.value).toBe("2");
    expect(formatFullSystemLogRecord(record)).toContain('"large": 9007199254740993');
  });

  it("maps common level aliases to semantic tones", () => {
    expect(logLevelTone("WARN")).toBe("warning");
    expect(logLevelTone("ERROR")).toBe("error");
    expect(logLevelTone("FATAL")).toBe("critical");
    expect(logLevelTone("TRACE")).toBe("debug");
  });

  it("counts files by source and URI across pages", () => {
    expect(uniqueSystemLogFiles([
      { source_id: 2, file_uri: "logs/a.jsonl" },
      { source_id: 2, file_uri: "logs/a.jsonl" },
      { source_id: 3, file_uri: "logs/a.jsonl" },
    ])).toHaveLength(2);
  });

  it("deduplicates repeated-page diagnostics while preserving same-response multiplicity", () => {
    const repeated = { code: "read_failed", message: "cannot read", uri: "logs/a.jsonl" };
    const other = { code: "read_failed", message: "cannot read", uri: "logs/b.jsonl" };
    expect(collectSystemLogReadErrors([
      { errors: [repeated, repeated] },
      { errors: [repeated, other] },
    ])).toEqual([repeated, repeated, other]);
  });

  it("defaults job viewers to job-only logs and opts into child dataflows explicitly", () => {
    expect(systemLogScopeParams(undefined, false)).toEqual({ log_session_scope: "all" });
    expect(systemLogScopeParams(undefined, true)).toEqual({ log_session_scope: "all", include_dataflow_logs: 1 });
    expect(systemLogScopeParams("df-orders", true)).toEqual({ log_session_scope: "all", dataflow_id: "df-orders" });
    expect(systemLogScopeParams(undefined, true, "run-42", "dataflow.started")).toEqual({
      log_session_scope: "all",
      dataflow_run_id: "run-42",
      event_name: "dataflow.started",
    });
  });
});
