import { describe, expect, it } from "vitest";
import { maintenanceEfficiencyOption, maintenancePageTestUtils } from "./pages/MaintenancePage";
import { reportChartPalette } from "./ReportChart";

const report = {
  summary: {
    timezone: "UTC",
    effective_grain: "day",
    date_range: { min: "2026-07-20", max: "2026-07-22" },
  },
  maintenance: {
    status_by_date: [{
      bucket: "2026-07-21T00:00:00Z",
      grain: "day",
      succeeded: 2,
      failed: 1,
      skipped: 0,
      running: 0,
      pending: 0,
      unknown: 0,
      total: 3,
      success_rate: 66.67,
    }],
    reclaim_by_date: [{
      bucket: "2026-07-21T00:00:00Z",
      grain: "day",
      files_added: 3,
      files_removed: 2,
      bytes_added: 4096,
      bytes_removed: 1024,
      runs: 1,
    }],
  },
} as any;

const filters = {
  range: "custom",
  grain: "day",
  startTime: "2026-07-20T00:00:00Z",
  endTime: "2026-07-22T23:59:59Z",
} as any;

describe("maintenance trends", () => {
  it("joins timestamp status buckets to normalized visible dates", () => {
    const option = maintenancePageTestUtils.maintenanceStatusTrendOption(report, filters, "UTC") as any;
    expect(option.xAxis.data).toEqual(["2026-07-20", "2026-07-21", "2026-07-22"]);
    expect(option.series[0].data).toEqual([0, 2, 0]);
    expect(option.series[1].data).toEqual([0, 1, 0]);
  });

  it("joins timestamp destination effect buckets without replacing evidence with zero", () => {
    const option = maintenancePageTestUtils.maintenanceReclaimTrendOption(report, filters, "UTC") as any;
    expect(option.series[0].data).toEqual([0, 3, 0]);
    expect(option.series[1].data).toEqual([0, 2, 0]);
  });

  it("keeps nullable and large maintenance metrics exact in gaps and tooltips", () => {
    const nativeReport = {
      ...report,
      maintenance: {
        status_by_date: [{
          bucket: "2026-07-21T00:00:00Z",
          grain: "day",
          succeeded: "9007199254740993",
          failed: null,
          skipped: 0,
          running: 0,
          pending: 0,
          unknown: 0,
          total: null,
          success_rate: null,
        }],
        reclaim_by_date: [{
          bucket: "2026-07-21T00:00:00Z",
          grain: "day",
          bytes_added: "9007199254740993",
          bytes_removed: null,
          files_added: null,
          files_removed: null,
          runs: "2",
        }],
      },
    } as any;
    const status = maintenancePageTestUtils.maintenanceStatusTrendOption(nativeReport, filters, "UTC") as any;
    const statusTooltip = status.tooltip.formatter([{ dataIndex: 1 }]);
    expect(status.series[0].data[1]).toBeGreaterThan(9e15);
    expect(status.series[1].data[1]).toBeNull();
    expect(statusTooltip).toContain("Succeeded: 9,007,199,254,740,993");
    expect(statusTooltip).toContain("Failed: -");

    const effects = maintenancePageTestUtils.maintenanceReclaimTrendOption(nativeReport, filters, "UTC", "bytes") as any;
    const effectsTooltip = effects.tooltip.formatter([{ dataIndex: 1 }]);
    expect(effects.series[0].data[1]).toBeGreaterThan(9e15);
    expect(effects.series[1].data[1]).toBeNull();
    expect(effectsTooltip).toContain("Added: 9,007,199,254,740,993 B");
    expect(effectsTooltip).toContain("Removed: -");
  });

  it("colors destination activity and effect tooltip values to match their chart series", () => {
    const chartReport = {
      ...report,
      maintenance: {
        status_by_date: [{
          bucket: "2026-07-21T00:00:00Z", grain: "day", succeeded: 2, failed: 1,
          skipped: 0, running: 0, pending: 0, unknown: 0, total: 3, success_rate: 66.67,
        }],
        reclaim_by_date: [{
          bucket: "2026-07-21T00:00:00Z", grain: "day", files_added: 5, files_removed: 2,
          bytes_added: 2048, bytes_removed: 512, runs: 1,
        }],
      },
    } as any;
    const activity = maintenancePageTestUtils.maintenanceStatusTrendOption(chartReport, filters, "UTC") as any;
    const activityTooltip = activity.tooltip.formatter([{ dataIndex: 1 }]);
    expect(activityTooltip).toContain(`<dd class="help-tone-neutral" style="color:${reportChartPalette.success}">2</dd>`);
    expect(activityTooltip).toContain(`<dd class="help-tone-neutral" style="color:${reportChartPalette.failed}">1</dd>`);

    const effects = maintenancePageTestUtils.destinationChangeTrendOption(chartReport, filters, "UTC", "files") as any;
    const effectsTooltip = effects.tooltip.formatter([{ dataIndex: 1 }]);
    expect(effectsTooltip).toContain(`<dd class="help-tone-neutral" style="color:${reportChartPalette.teal}">5</dd>`);
    expect(effectsTooltip).toContain(`<dd class="help-tone-neutral" style="color:${reportChartPalette.blue}">2</dd>`);
  });

  it("renders destination storage paths as full-width path values in both destination tooltips", () => {
    const path = "./usecase-sim/.runtime/data/output/delta/orders_read_delta_schema_evolve";
    const row = {
      target: "destination-1", destination_name: "Lakehouse", destination_format: "delta", destination_path: path,
      bytes_added: 1024, paired_bytes_added: 1024, paired_bytes_runtime: 2, paired_bytes_runs: 1, latest_status: "failed",
    };
    const top = maintenancePageTestUtils.destinationOutcomeOption([row], "bytes", "added") as any;
    const efficiency = maintenanceEfficiencyOption([row], "linear", "bytes", "added") as any;
    for (const option of [top, efficiency]) {
      const html = option.tooltip.formatter({ dataIndex: 0 });
      expect(html).toContain('class="help-metric-path"');
      expect(html).toContain(`<dd class="help-tone-neutral">${path}</dd>`);
      if (option === efficiency) {
        expect(html).toContain(`<dt>Point color (latest status)</dt><dd class="help-tone-neutral" style="color:${reportChartPalette.failed}">Failed</dd>`);
        expect(html).toContain(`<dt>Root runtime</dt><dd class="help-tone-neutral">2s</dd>`);
        expect(html).not.toContain("seconds");
      }
    }

    const longTable = maintenancePageTestUtils.destinationOutcomeOption([{
      ...row,
      destination_table: "datatype_decimal_spark_iceberg",
      destination_full_table: "`local_catalog`.`default`.`datatype_decimal_spark_iceberg`",
    }], "bytes", "added") as any;
    expect(longTable.tooltip.formatter({ dataIndex: 0 })).toContain('class="help-metric-multiline"');
  });
});
