import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { describe, expect, it } from "vitest";
import { DataTable, monitoringTableTestUtils, type TableColumn } from "./MonitoringCharts";

describe("monitoring table sizing", () => {
  it("floors a fractional panel width so a fitted table does not create a false horizontal scrollbar", () => {
    expect(monitoringTableTestUtils.normalizeTableContainerWidth(632.8125)).toBe(632);
    expect(monitoringTableTestUtils.normalizeTableContainerWidth(550.8125)).toBe(550);
  });

  it("keeps a table inside the content box when a native vertical scrollbar consumes width", () => {
    const contentWidth = monitoringTableTestUtils.calculateElementContentWidth(1618, 1603);

    expect(contentWidth).toBe(1603);
    expect(monitoringTableTestUtils.normalizeTableContainerWidth(contentWidth)).toBe(1603);
  });

  it("auto-fits runtime from the widest visible line instead of joining both lines", () => {
    const rows = [
      { runtime_context: ["Local · Spark", "FileProvider"] },
      { runtime_context: ["Local · Polars", "DatabaseProvider"] }
    ];
    const columns: Array<TableColumn<(typeof rows)[number]>> = [
      {
        key: "runtime_context",
        label: "Runtime",
        autoFit: true,
        minWidth: 72,
        maxWidth: 170,
        measureValue: (row) => row.runtime_context
      }
    ];

    const widths = monitoringTableTestUtils.calculateAutoFitWidths(rows, columns);

    expect(widths.runtime_context).toBeGreaterThan(100);
    expect(widths.runtime_context).toBeLessThan(170);
  });

  it("auto-fits custom rendered cells from their longest visible line", () => {
    const rows = [{ id: "one", runtime: "ignored" }];
    const columns: Array<TableColumn<(typeof rows)[number]>> = [
      {
        key: "runtime",
        label: "Runtime",
        autoFit: true,
        minWidth: 80,
        maxWidth: 190,
        render: () => null,
        measureValue: () => ["short", "provider-with-long-name"]
      }
    ];

    const widths = monitoringTableTestUtils.calculateAutoFitWidths(rows, columns);

    expect(widths.runtime).toBeGreaterThan(100);
    expect(widths.runtime).toBeLessThanOrEqual(190);
  });

  it("does not reserve sortable-header chrome for a static auto-fit column", () => {
    const rows = [{ runtime: "py" }];
    const columns: Array<TableColumn<(typeof rows)[number]>> = [
      {
        key: "runtime",
        label: "Runtime",
        autoFit: true,
        minWidth: 72,
        maxWidth: 170,
        measureValue: () => ["py", "analyst"]
      }
    ];

    const widths = monitoringTableTestUtils.calculateAutoFitWidths(rows, columns);

    expect(widths.runtime).toBeLessThan(90);
  });

  it("preserves auto-fit columns when the table is wider than its container", () => {
    const columns: Array<TableColumn<{ runtime: string }>> = [
      { key: "runtime", label: "Runtime", autoFit: true, minWidth: 72, maxWidth: 170 },
      { key: "stages", label: "Stages", minWidth: 110, maxWidth: 160, fillPriority: "last" },
      { key: "issue", label: "Issue", minWidth: 140, fillPriority: "last" }
    ];
    const baseWidths = { runtime: 106, stages: 110, issue: 140 };

    const widths = monitoringTableTestUtils.distributeTableWidth(columns, baseWidths, {}, 330);

    expect(widths.runtime).toBe(106);
    expect(Object.values(widths).reduce((total, width) => total + width, 0)).toBeGreaterThan(330);
  });

  it("allocates spare width to fill columns without widening Runtime", () => {
    const columns: Array<TableColumn<{ runtime: string }>> = [
      { key: "runtime", label: "Runtime", autoFit: true, minWidth: 72, maxWidth: 170 },
      { key: "stages", label: "Stages", minWidth: 110, maxWidth: 160, fillPriority: "last" },
      { key: "issue", label: "Issue", minWidth: 140, fillPriority: "last" }
    ];
    const baseWidths = { runtime: 106, stages: 110, issue: 140 };

    const widths = monitoringTableTestUtils.distributeTableWidth(columns, baseWidths, {}, 456);

    expect(widths.runtime).toBe(106);
    expect(widths.stages).toBeGreaterThan(110);
    expect(widths.issue).toBeGreaterThan(140);
  });

  it("sorts a synthetic display column by its declared sort key", () => {
    const rows = [
      { id: "later", start_time: "2026-07-14T12:00:00Z" },
      { id: "earlier", start_time: "2026-07-14T10:00:00Z" }
    ];
    const columns: Array<TableColumn<(typeof rows)[number]>> = [
      { key: "time_window", label: "Start / End", sortable: true, sortKey: "start_time" }
    ];

    const sorted = monitoringTableTestUtils.sortRows(rows, columns, { sortBy: "start_time", sortDir: "asc" });

    expect(sorted.map((row) => row.id)).toEqual(["earlier", "later"]);
  });

  it("compacts opt-in numeric cells without changing raw-value sorting", () => {
    const rows: Array<Record<string, unknown>> = [{ id: "large", count: 898500000 }, { id: "small", count: 1200 }];
    const columns: Array<TableColumn<Record<string, unknown>>> = [
      { key: "count", label: "Count", sortable: true, autoFit: true }
    ];

    const markup = renderToStaticMarkup(createElement(DataTable, { rows, columns, compactNumbers: true }));
    const sorted = monitoringTableTestUtils.sortRows(rows, columns, { sortBy: "count", sortDir: "asc" });

    expect(markup).toContain('title="898,500,000"');
    expect(markup).toContain(">898.5M</span>");
    expect(sorted.map((row) => row.id)).toEqual(["small", "large"]);
  });

  it("keeps the default DataTable numeric presentation exact", () => {
    const markup = renderToStaticMarkup(
      createElement(DataTable, { rows: [{ count: 898500000 }], columns: [{ key: "count", label: "Count" }] })
    );

    expect(markup).toContain(">898,500,000</td>");
    expect(markup).not.toContain("898.5M");
  });
});
