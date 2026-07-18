import { describe, expect, it } from "vitest";
import {
  freshnessDrawerHealth,
  freshnessEndpointLabel,
  freshnessRunTimeLines,
  isFreshnessWatermarkConfigured,
} from "./MonitoringDetailDrawer";
import { formatPhasePercent } from "./monitoringShared";

describe("Freshness registry drawer semantics", () => {
  it("formats runtime phase contribution with one decimal place", () => {
    expect(formatPhasePercent(47.129)).toBe("47.1%");
    expect(formatPhasePercent(50)).toBe("50%");
    expect(formatPhasePercent(0.04)).toBe("0%");
  });

  it("measures Freshness run time from separate start and end lines", () => {
    expect(freshnessRunTimeLines({
      start_time: "2026-07-14T07:19:22Z",
      end_time: "2026-07-14T07:19:24Z",
    }, "UTC")).toEqual([
      "2026-07-14 07:19:22 UTC",
      "→ 2026-07-14 07:19:24 UTC",
    ]);
  });

  it("uses the fixed seven-day boundary for current and stale rows", () => {
    expect(freshnessDrawerHealth({ latest_freshness_at: "2026-07-15", latest_run_status: "succeeded", age_days: 7 })).toEqual({
      label: "Current",
      tone: "success",
    });
    expect(freshnessDrawerHealth({ latest_freshness_at: "2026-07-15", latest_run_status: "succeeded", age_days: 7.01 })).toEqual({
      label: "Stale",
      tone: "warning",
    });
  });

  it("prioritizes latest-run and watermark issues over age", () => {
    expect(freshnessDrawerHealth({ latest_run_at: "2026-07-15", latest_run_status: "failed", age_days: 1 })).toEqual({
      label: "Needs review",
      tone: "failed",
    });
    expect(freshnessDrawerHealth({ latest_run_at: "2026-07-15", latest_run_status: "succeeded", movement_state: "incomplete", age_days: 1 })).toEqual({
      label: "Needs review",
      tone: "warning",
    });
    expect(freshnessDrawerHealth({})).toEqual({ label: "No evidence", tone: "neutral" });
  });

  it("collapses not-configured watermark evidence but keeps configured evidence", () => {
    expect(isFreshnessWatermarkConfigured({ coverage_state: "not_configured", source_watermark_before: { id: 1 } })).toBe(false);
    expect(isFreshnessWatermarkConfigured({ movement_state: "advanced", source_watermark_before: { id: 1 } })).toBe(true);
  });

  it("builds an original-case route and uses representative SQL and Python labels", () => {
    expect(freshnessEndpointLabel({ source_connection_name: "Connection_A", source_format: "sql", source_query: "select 1" }, "source"))
      .toBe("Connection_A - sql query");
    expect(freshnessEndpointLabel({ source_connection_name: "Fn_Source", source_format: "python_function", source_python_function: "pkg.read" }, "source"))
      .toBe("Fn_Source - python function");
    expect(freshnessEndpointLabel({ destination_connection_name: "Lakehouse_Gold", destination_table: "Orders" }, "destination"))
      .toBe("Lakehouse_Gold - Orders");
  });
});
