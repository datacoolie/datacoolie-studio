import { describe, expect, it } from "vitest";

import type {
  MonitoringDataflowRecordsResponseTransport,
  MonitoringDataflowRegistryResponseTransport,
  MonitoringFilterOptionsResponseTransport,
  MonitoringLatestStatusResponseTransport,
  MonitoringSystemLogsResponseTransport,
} from "./contractTypes";
import {
  formatRawRecordJson,
  isLosslessJsonNumber,
  monitoringAttempts,
  rawRecordJsonForCopy,
  parseRawRecordJson,
  toLatestStatus,
  toMonitoringDataflowRecords,
  toMonitoringDataflowRegistry,
  toMonitoringFilterOptions,
  toMonitoringSystemLogs,
} from "./monitoringAdapters";

describe("Monitoring API adapters", () => {
  it("preserves the actual reader generation and anchored registry scope", () => {
    const summary = { records: 1, total_records: 1, limit: 100, offset: 0,
      analytics_generation: "generation-A", as_of: "2026-10-14T00:00:00Z" };
    const record = { dataflow_id: "flow", dataflow_run_id: "run" };
    expect(toMonitoringDataflowRecords({ records: [record], errors: [], summary } as unknown as MonitoringDataflowRecordsResponseTransport).summary).toEqual(summary);
    const response = { section: "overview", records: [{ dataflow_id: "flow", recent_runs: [record], profile_snapshot: { ...record, _source_id: 1, outside_filter: true } }], errors: [], summary } as unknown as MonitoringDataflowRegistryResponseTransport;
    const result = toMonitoringDataflowRegistry(response);
    expect(result.section).toBe("overview");
    expect(result.summary).toEqual(summary);
    expect(result.records[0].recent_runs).toEqual([record]);
    expect(result.records[0].profile_snapshot?.outside_filter).toBe(true);
  });

  it("normalizes sparse record envelopes and preserves extension fields", () => {
    const response = {
      records: [{ dataflow_id: "flow-1", provider_metric: 3 }],
      errors: [{ code: "warning" }],
      summary: { records: 1, total_records: 4, limit: 10, offset: 0, cache: null },
    } as unknown as MonitoringDataflowRecordsResponseTransport;

    expect(toMonitoringDataflowRecords(response)).toEqual({
      records: [{ dataflow_id: "flow-1", provider_metric: 3 }],
      errors: [{ code: "warning" }],
      summary: { records: 1, total_records: 4, limit: 10, offset: 0 },
    });
  });

  it("fills optional filter, latest-status, and system-log collections", () => {
    const filterOptions = toMonitoringFilterOptions({
      options: { status: [{ value: "ok", label: "OK", count: null }] },
      summary: { source: "duckdb_filter_values", fields: 1 },
    } as unknown as MonitoringFilterOptionsResponseTransport);
    const latestStatus = toLatestStatus({
      latest_by_id: { "flow-1": { dataflow_id: "flow-1" } },
      latest_by_name: {},
      ambiguous_names: [],
      errors: [],
    } as unknown as MonitoringLatestStatusResponseTransport);
    const systemLogs = toMonitoringSystemLogs({
      records: [{ message: "hello" }],
      total: 1,
      files: [],
      errors: [{ code: "read_failed" }],
    } as unknown as MonitoringSystemLogsResponseTransport);

    expect(filterOptions.options.status).toEqual([{ value: "ok", label: "OK" }]);
    expect(latestStatus.latest_by_id["flow-1"].dataflow_id).toBe("flow-1");
    expect(systemLogs.errors).toEqual([{ code: "read_failed" }]);
  });

  it("parses raw evidence losslessly while preserving authored number/string types and copy text", () => {
    const raw = '{"metrics":{"rows_read":9007199254740993,"tag":"9007199254740993"},"attempts":[{"attempt_number":1}],"diagnostic_issues":[]}';
    const parsed = parseRawRecordJson(raw);
    const root = parsed && typeof parsed === "object" && !Array.isArray(parsed) && !isLosslessJsonNumber(parsed)
      ? parsed as Record<string, unknown>
      : {};
    expect(root.metrics).toBeTruthy();
    const metrics = root.metrics as Record<string, unknown>;
    expect(isLosslessJsonNumber(metrics.rows_read)).toBe(true);
    expect(metrics.tag).toBe("9007199254740993");
    expect(monitoringAttempts({ raw_record_json: raw })).toHaveLength(1);
    expect(monitoringAttempts({ latest_raw_record_json: raw })).toHaveLength(1);
    expect(rawRecordJsonForCopy(raw)).toBe(raw);
    expect(formatRawRecordJson(raw)).toContain('"rows_read": 9007199254740993');
  });
});
