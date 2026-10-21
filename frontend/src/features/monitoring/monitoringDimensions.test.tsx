import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { monitoringDimensionLabel, monitoringRawDimensionLabel } from "./monitoringDimensions";
import { MonitoringFilterBar } from "./MonitoringFilterBar";
import { DEFAULT_MONITORING_FILTERS } from "./monitoringFilters";
import { RuntimePhaseContribution, monitoringEndpointPresentation } from "./components/monitoringPresentation";

describe("Monitoring missing dimensions", () => {
  it("labels missing members and lossless escaped ordinary values separately", () => {
    for (const value of [null, undefined, "", " \t\n", "\u00a0", "\u2003", "\ufeff", "__monitoring_unknown__"]) expect(monitoringDimensionLabel(value)).toBe("Unknown");
    expect(monitoringDimensionLabel("unknown")).toBe("unknown (value)");
    expect(monitoringDimensionLabel("Unknown")).toBe("Unknown (value)");
    expect(monitoringDimensionLabel("__monitoring_value__:617c62")).toBe("a|b");
    expect(monitoringDimensionLabel("__monitoring_context__:[\"__monitoring_unknown__\",\"etl\"]")).toBe("Unknown · etl");
    expect(monitoringDimensionLabel("__monitoring_value__:ff")).toBe("__monitoring_value__:ff");
  });

  it("preserves ordinary reserved names and composite punctuation exactly once", () => {
    expect(monitoringDimensionLabel("a__monitoring_unknown__b")).toBe("a__monitoring_unknown__b");
    expect(monitoringDimensionLabel("__monitoring_value__:5f5f6d6f6e69746f72696e675f756e6b6e6f776e5f5f")).toBe("__monitoring_unknown__");
    expect(monitoringDimensionLabel('__monitoring_context__:["a]b","c"]')).toBe("a]b · c");
    expect(monitoringRawDimensionLabel("__monitoring_unknown__")).toBe("__monitoring_unknown__");
    const endpoint = monitoringEndpointPresentation({ source_name: "__monitoring_unknown__", source_format: "__monitoring_unknown__" } as any, "source");
    expect(endpoint.connection).toBe("__monitoring_unknown__");
    expect(endpoint.format).toBe("__monitoring_unknown__");
  });

  it("renders selected missing slicers from backend labels without internal keys", () => {
    const html = renderToStaticMarkup(<MonitoringFilterBar environmentId={1} filters={{ ...DEFAULT_MONITORING_FILTERS, operationType: "__monitoring_unknown__" }} options={{ options: { operation_type: [{ value: "__monitoring_unknown__", label: "Unknown", count: 6 }] }, summary: { source: "duckdb_filter_values", fields: 1 }, errors: [] }} searchOptions={[]} onChange={() => {}} />);
    expect(html).toContain('<span>Unknown</span>');
    expect(html).not.toContain("__monitoring_unknown__");
  });

  it("keeps missing and literal Unknown phase groups as separate rows", () => {
    const rows = ["__monitoring_unknown__", "Unknown", "unknown"].map((value) => ({ operation_type: value, phase_runs_eligible: 1, phase_runs_measured: 0, total_duration_seconds: null }));
    const html = renderToStaticMarkup(<RuntimePhaseContribution rows={rows} showLegend={false} />);
    expect(html).toContain(">Unknown</strong>");
    expect(html).toContain("Unknown (value)");
    expect(html).toContain("unknown (value)");
    expect(html).not.toContain("__monitoring_unknown__");
  });
});
