import { describe, expect, it } from "vitest";import { formatFreshnessAge, freshnessDrawerHealth, freshnessRunTimeLines } from "./details/detailPrimitives";
import { freshnessEndpointLabel, isFreshnessWatermarkConfigured } from "./details/FreshnessDetails";import { formatPhasePercent, monitoringEndpointPresentation } from "./components/monitoringPresentation";
import { monitoringConnectionStageClass } from "./monitoringStagePresentation";

describe("Freshness registry drawer semantics", () => {
  it.each([
    [null, null, "-"], [undefined, undefined, "-"], ["", "", "-"],
    [" ", null, "-"], [false, null, "-"], [0, null, "0s"],
    [null, 2, "2d"], ["invalid", 1, "1d"], [90, null, "1.5m"],
  ])("preserves unknown and reported zero ages (%s, %s)", (seconds, days, expected) => {
    expect(formatFreshnessAge(seconds, days)).toBe(expected);
  });

  it.each([undefined, null, "unknown", ""])("does not turn an unreported latest status into a prior success (%s)", (status) => {
    expect(freshnessDrawerHealth({ latest_run_at: "2026-10-02", latest_freshness_at: "2026-10-01",
      latest_freshness_status: "succeeded", latest_run_status: status, age_days: 1 }))
      .toEqual({ label: "Needs review", tone: "warning" });
  });

  it.each(["succeeded", "skipped"])("requires freshness age evidence for %s", (status) => {
    expect(freshnessDrawerHealth({ latest_run_at: "2026-10-02", latest_run_status: status,
      age_seconds: null, age_days: null })).toEqual({ label: "Needs review", tone: "warning" });
    expect(freshnessDrawerHealth({ latest_run_at: "2026-10-02", latest_run_status: status,
      age_seconds: 0, age_days: 0 })).toEqual({ label: "Current", tone: "success" });
  });

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

  it("uses connection stage tokens for endpoint surfaces without using flow stage", () => {
    expect(monitoringConnectionStageClass("wwi_bronze_orders")).toBe("monitor-stage-bronze");
    expect(monitoringConnectionStageClass("wwi_bronze_orders_silver_summary")).toBe("monitor-stage-bronze");
    expect(monitoringConnectionStageClass("wwi_silver_orders_bronze_archive")).toBe("monitor-stage-silver");
    expect(monitoringConnectionStageClass("external_resource")).toBe("");
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
    expect(freshnessDrawerHealth({ latest_run_at: "2026-07-15", latest_run_status: "succeeded", movement_state: "unknown", watermark_issue_state: "insufficient_evidence", age_days: 1 })).toEqual({
      label: "Needs review",
      tone: "warning",
    });
    expect(freshnessDrawerHealth({ latest_run_at: "2026-07-15", latest_run_status: "succeeded", save_status: "save_error", age_days: 1 })).toEqual({
      label: "Needs review",
      tone: "failed",
    });
    expect(freshnessDrawerHealth({ latest_run_at: "2026-07-15", latest_run_status: "succeeded", movement_state: "unknown", save_status: "save_error", age_days: 1 })).toEqual({
      label: "Needs review",
      tone: "failed",
    });
    expect(freshnessDrawerHealth({})).toEqual({ label: "No evidence", tone: "neutral" });
  });

  it("collapses not-configured watermark evidence but keeps configured evidence", () => {
    expect(isFreshnessWatermarkConfigured({ coverage_state: "not_configured", saved_watermark: { id: 1 } })).toBe(false);
    expect(isFreshnessWatermarkConfigured({ movement_state: "advanced", loaded_watermark: { id: 1 }, save_status: "saved" })).toBe(true);
  });

  it("builds an original-case route and uses representative SQL and Python labels", () => {
    expect(freshnessEndpointLabel({ source_connection_name: "Connection_A", source_format: "sql", source_query: "select 1" }, "source"))
      .toBe("Connection_A - sql query");
    expect(freshnessEndpointLabel({ source_connection_name: "Fn_Source", source_format: "python_function", source_python_function: "pkg.read" }, "source"))
      .toBe("Fn_Source - python function");
    expect(freshnessEndpointLabel({ destination_connection_name: "Lakehouse_Gold", destination_table: "Orders" }, "destination"))
      .toBe("Lakehouse_Gold - Orders");
  });

  it("uses one asset resolver for registry cells and drawer routes", () => {
    const source = monitoringEndpointPresentation({
      source_name: "landing",
      source_format: "parquet",
      source_path: "/lakehouse/raw/orders/part-001.parquet",
    }, "source");
    const destination = monitoringEndpointPresentation({
      destination_name: "warehouse",
      destination_format: "delta",
      destination_full_table: "catalog.curated.orders",
    }, "destination");

    expect(source.locator).toBe("part-001.parquet");
    expect(destination.locator).toBe("orders");
  });
});
