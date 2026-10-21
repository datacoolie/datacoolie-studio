import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { DestinationSignalsCell, destinationSignalsHelpContent } from "./destinationSignalsPresentation";
import { destinationRegistryHelpTestUtils as tooltips } from "./pages/MaintenanceRegistryTable";
import { HelpContentView } from "../../shared/components/Help";
import { reportChartPalette } from "./ReportChart";

describe("destination registry tooltips", () => {
  it("keeps destination format concise and avoids repeating the same format", () => {
    const row = {
      target: "asset:orders",
      destination_name: "warehouse",
      destination_table: "orders",
      destination_format: "parquet",
      format_variants: ["parquet"],
      format_unknown_runs: 0,
      identity_reason: "Matched from the captured table identity",
    };
    const content = tooltips.destinationRegistryHelpContent(row, {
      id: "asset:orders", name: "orders", connection: "warehouse", format: "parquet", context: "",
      subtitle: "warehouse", locator: "orders", locatorLabel: "Table", path: "", fullIdentity: "warehouse · orders", shortId: "orders",
    });

    expect(content.metrics?.filter(metric => metric.label === "Format")).toHaveLength(1);
    expect(content.metrics?.some(metric => metric.label === "Latest format")).toBe(false);
    expect(content.metrics?.some(metric => metric.label === "Known formats")).toBe(false);
    expect(content.metrics?.some(metric => metric.label === "Unknown format runs")).toBe(false);
    expect(content.note).toContain("Identity evidence: Matched from the captured table identity");
  });

  it("normalizes unknown operation sentinels and formats timestamps for display", () => {
    const content = tooltips.latestOperationHelpContent({
      latest_time: "2026-10-21T01:02:03Z",
      root_operation_type: "__monitoring_unknown__",
      latest_status: "failed",
      destination_operation_type: "etl",
      destination_status: "succeeded",
    }, "UTC");
    const values = new Map(content.metrics?.map(metric => [metric.label, metric.value]));

    expect(values.get("Timestamp")).not.toBe("2026-10-21T01:02:03Z");
    expect(values.get("Root operation")).toBe("Unknown");
    expect(values.get("Root status")).toBe("Failed");
    expect(content.metrics?.find(metric => metric.label === "Root status")?.valueColor).toBe("#b92d32");
    expect(content.metrics?.find(metric => metric.label === "Destination phase status")?.valueColor).toBe("#17735c");
  });

  it("uses the same series colors as the registry rows and chart palette", () => {
    const content = tooltips.workloadRowsHelpContent({
      workload_rows_read: 1200,
      workload_rows_read_measured_runs: 2,
      workload_rows_read_eligible_runs: 3,
      workload_rows_written: 400,
      workload_rows_written_measured_runs: 3,
      workload_rows_written_eligible_runs: 3,
    });
    const metrics = new Map(content.metrics?.map(metric => [metric.label, metric]));

    expect(metrics.get("Read")?.valueColor).toBe(reportChartPalette.read);
    expect(metrics.get("Written")?.valueColor).toBe(reportChartPalette.written);
    expect(metrics.get("Read")?.value).toBe("1,200*");
    expect(metrics.get("Read coverage")?.value).toBe("2 / 3 eligible reports");
  });

  it("omits absent write-layout placeholders and keeps recorded configuration", () => {
    const absent = tooltips.writeLayoutHelpContent({});
    expect(absent.body).toContain("No write configuration evidence");
    expect(absent.metrics).toBeUndefined();

    const recorded = tooltips.writeLayoutHelpContent({ layout: { load_type: "append" } });
    expect(recorded.metrics?.map(metric => metric.label)).toEqual(["Write mode", "Mode origin"]);
    expect(recorded.metrics?.find(metric => metric.label === "Mode origin")?.value).toBe("Captured");
    expect(renderToStaticMarkup(<HelpContentView content={recorded} />)).not.toContain("Unknown");
  });

  it("shows signal severity in the shared hover tooltip with matching row color", () => {
    const row = { destination_signals: [{
      code: "destination_failed", severity: "error", count: 2, category: "operation", details: ["Two destination writes failed"],
    }] };
    const content = destinationSignalsHelpContent(row);
    const markup = renderToStaticMarkup(<><DestinationSignalsCell row={row} /><HelpContentView content={content} /></>);

    expect(content.metrics?.[0].valueColor).toBe("var(--color-danger)");
    expect(markup).toContain("2 destination failures");
    expect(markup).toContain("style=\"color:var(--color-danger)\"");
    expect(markup).not.toContain(" title=");
  });
});
