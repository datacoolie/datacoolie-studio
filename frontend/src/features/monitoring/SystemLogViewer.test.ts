import { describe, expect, it } from "vitest";
import { formatSystemLogRecord, logLevelTone, systemLogParts, systemLogScopeParams } from "./SystemLogViewer";

describe("SystemLogViewer formatting", () => {
  it("formats the structured log contract and keeps an empty dataflow placeholder", () => {
    const record = {
      ts: "2026-07-14T14:19:04+07:00",
      level: "info",
      logger: "DataCoolie.driver",
      func: "load_dataflows",
      line: 270,
      msg: "Loading dataflows",
    };

    expect(systemLogParts(record, "Asia/Saigon").dataflowId).toBe("-");
    expect(formatSystemLogRecord(record, "Asia/Saigon")).toBe(
      "2026-07-14 14:19:04 GMT+7 - [INFO] - DataCoolie.driver:load_dataflows:270 - [-] - Loading dataflows",
    );
  });

  it("uses dataflow_id and message aliases when present", () => {
    const parts = systemLogParts({ dataflow_id: "df-orders", message: "Done" });
    expect(parts.dataflowId).toBe("df-orders");
    expect(parts.message).toBe("Done");
  });

  it("maps common level aliases to semantic tones", () => {
    expect(logLevelTone("WARN")).toBe("warning");
    expect(logLevelTone("ERROR")).toBe("error");
    expect(logLevelTone("FATAL")).toBe("critical");
    expect(logLevelTone("TRACE")).toBe("debug");
  });

  it("defaults job viewers to job-only logs and opts into child dataflows explicitly", () => {
    expect(systemLogScopeParams(undefined, false)).toEqual({});
    expect(systemLogScopeParams(undefined, true)).toEqual({ include_dataflow_logs: 1 });
    expect(systemLogScopeParams("df-orders", true)).toEqual({ dataflow_id: "df-orders" });
  });
});
