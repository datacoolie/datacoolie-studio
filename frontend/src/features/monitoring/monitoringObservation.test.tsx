import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { normalizeMonitoringPage } from "./monitoringQueries";
import { metricObservation } from "./components/monitoringObservation";
import { MeasurementCoverage, ObservedNumberValue } from "./components/monitoringPresentation";
import { mergeVolumeTrendRows, workloadVolumeTrendOption } from "./components/monitoringChartOptions";
import { chartTooltipContent } from "./components/chartTooltip";
import { DEFAULT_MONITORING_FILTERS } from "./monitoringFilters";
import { VolumePage, VolumeRoutePanel, storageDeltaTrendOption, topDataflowsByWorkloadOption, workloadMixOption } from "./pages/VolumePage";

const filters = { ...DEFAULT_MONITORING_FILTERS, range: "custom" as const, grain: "day" as const, startTime: "2026-10-09", endTime: "2026-10-09" };
const dateRange = { min: "2026-10-09", max: "2026-10-09" };
const bucket = { date: "2026-10-09", bucket: "2026-10-09", grain: "day" };
const EMPTY_MONITORING_REPORT = normalizeMonitoringPage({ summary: {} } as never);
type Chart = { series: Array<{ data: Array<number | null>; showSymbol?: boolean }>; tooltip: { formatter: (params: unknown) => string } };

describe("Monitoring sparse observations", () => {
  it("uses an observed subtotal only when a measurement exists and preserves complete zero", () => {
    expect(metricObservation(null, "42", "1", "2")).toMatchObject({ value: "42", partial: true, coverage: "1 / 2 runs measured" });
    expect(metricObservation(null, "0", "0", "2").value).toBeNull();
    expect(metricObservation(undefined, "42", undefined, "2").value).toBeNull();
    expect(metricObservation("0", "42", "0", "0")).toMatchObject({ value: "0", partial: false });
  });

  it("renders large observed values exactly with explicit coverage and no false zero", () => {
    const row = { total_rows_read: null, observed_rows_read: "9007199254740993", rows_read_measured: "1", rows_read_eligible: "2" };
    const markup = renderToStaticMarkup(<><ObservedNumberValue row={row} field="rows_read" totalField="total_rows_read" /><MeasurementCoverage row={row} field="rows_read" /></>);
    expect(markup).toContain('aria-description="9,007,199,254,740,993"');
    expect(markup).toContain("observed</span>");
    expect(markup).toContain("1 / 2 runs measured");
    const unknown = renderToStaticMarkup(<ObservedNumberValue row={{ ...row, rows_read_measured: "0", observed_rows_read: "0" }} field="rows_read" totalField="total_rows_read" />);
    expect(unknown).toContain(">-</span>");
    expect(unknown).not.toContain("observed</span>");
  });

  it("labels numeric totals as observed when some runs lack measurements", () => {
    const row = { total_rows_read: "100", observed_rows_read: "100", rows_read_measured: "1", rows_read_eligible: "2" };
    const markup = renderToStaticMarkup(<ObservedNumberValue row={row} field="rows_read" totalField="total_rows_read" />);
    expect(markup).toContain('aria-description="100"');
    expect(markup).toContain("observed</span>");
    expect(markup).toContain("1 / 2 runs measured");
    expect(metricObservation("0", null, "1", "2")).toMatchObject({ value: "0", partial: true });
    expect(metricObservation("100", "100", "2", "2")).toMatchObject({ value: "100", partial: false });
  });

  it("compares measurement coverage exactly above the safe integer range", () => {
    expect(metricObservation("0", "0", "9007199254740992", "9007199254740993").partial).toBe(true);
  });

  it("plots sparse row subtotals with exact tooltip values and leaves unmeasured bytes unknown", () => {
    const option = workloadVolumeTrendOption(
      [{ ...bucket, rows_read: null, observed_rows_read: "45805", rows_read_measured: "1794", rows_read_eligible: "1825", rows_written: null, observed_rows_written: "9007199254740993", rows_written_measured: "610", rows_written_eligible: "1825" }],
      [{ ...bucket, bytes_added: null, bytes_removed: null }], filters, dateRange, "UTC", "day",
    ) as Chart;
    expect(option.series.map((series) => series.data[0])).toEqual([45805, Number("9007199254740993"), null, null]);
    const tooltip = option.tooltip.formatter([{ dataIndex: 0 }]);
    expect(tooltip).toContain("Rows read: 45,805; 1,794 / 1,825 runs");
    expect(tooltip).toContain("Rows written: 9,007,199,254,740,993");
    expect(tooltip).toContain("Lakehouse bytes added: -");
    expect(tooltip).not.toContain("measured");
    expect(tooltip).not.toContain("observed subtotal");
  });

  it("preserves storage fields through the real merge and chart path", () => {
    const storage = { ...bucket, bytes_added: "104755", bytes_removed: "0", net_bytes: "104755", files_added: "14", files_removed: "0" };
    const merged = mergeVolumeTrendRows([bucket], [storage])[0];
    expect(merged).toMatchObject(storage);
    const hourlyBucket = "2026-10-09T10:00:00";
    expect(mergeVolumeTrendRows([{ ...bucket, bucket: hourlyBucket }], [{ ...storage, bucket: hourlyBucket }])[0])
      .toMatchObject({ date: hourlyBucket, bucket: hourlyBucket, files_added: "14" });
    const report = { ...EMPTY_MONITORING_REPORT, summary: { ...EMPTY_MONITORING_REPORT.summary, date_range: dateRange }, volume: { ...EMPTY_MONITORING_REPORT.volume, rows_by_date: [bucket], bytes_by_date: [storage] } };
    const option = storageDeltaTrendOption(report, filters, "UTC") as Chart;
    expect(option.series.map((series) => series.data)).toEqual([[104755], [14]]);
    expect(option.series[1].showSymbol).toBe(true);
    expect(option.tooltip.formatter([{ dataIndex: 0 }])).toContain("Files added / removed: 14 / 0");
    const tooltip = chartTooltipContent(option.tooltip.formatter)?.([{ dataIndex: 0 }]);
    expect(tooltip?.metrics?.map(({ label, valueColor }) => [label, valueColor])).toEqual([
      ["Net bytes", "#155e59"], ["Bytes added", "#155e59"], ["Bytes removed", "#c94a4f"], ["Files added / removed", "#3d6fa8"],
    ]);
    const unknownReport = { ...report, volume: { ...report.volume, bytes_by_date: [{ ...storage, net_bytes: null, bytes_added: null, files_added: null }] } };
    expect((storageDeltaTrendOption(unknownReport, filters, "UTC") as Chart).series.map((series) => series.data)).toEqual([[null], [null]]);
  });

  it("keeps measured zero distinct from missing file counts", () => {
    const report = { ...EMPTY_MONITORING_REPORT, summary: { ...EMPTY_MONITORING_REPORT.summary, date_range: dateRange }, volume: { ...EMPTY_MONITORING_REPORT.volume, bytes_by_date: [{ ...bucket, net_bytes: "0", files_added: "0", files_removed: "0" }] } };
    expect((storageDeltaTrendOption(report, filters, "UTC") as Chart).series.map((series) => series.data)).toEqual([[0], [0]]);
  });

  it("uses sparse workload mix observations without drawing unknown as zero", () => {
    const option = workloadMixOption([{ workload_type: "etl", rows_read: null, observed_rows_read: "12", rows_read_measured: "1", rows_read_eligible: "2", rows_written: null, observed_rows_written: "0", rows_written_measured: "0", rows_written_eligible: "2" }]) as Chart;
    expect(option.series.map((series) => series.data)).toEqual([[12], [null]]);
    expect(option.tooltip.formatter({ dataIndex: 0 })).toContain("Rows read: 12; 1 / 2 runs");
    expect(option.tooltip.formatter({ dataIndex: 0 })).not.toContain("measured");
    expect(option.tooltip.formatter({ dataIndex: 0 })).not.toContain("observed subtotal");
    const tooltip = chartTooltipContent(option.tooltip.formatter)?.({ dataIndex: 0 });
    expect(tooltip?.metrics?.map(({ label, valueColor }) => [label, valueColor])).toEqual([
      ["Runs", undefined], ["Rows read", "#2563eb"], ["Rows written", undefined],
    ]);
  });

  it("keeps route volume tooltips focused on values without observation jargon", () => {
    const markup = renderToStaticMarkup(<VolumeRoutePanel rows={[{
      source_name: "source_conn", destination_name: "destination_conn", runs: 2, skipped: 0,
      rows_read: "12", rows_written: "9", lakehouse_rows_written: "9", bytes_added: "2107384",
      bytes_removed: "0", files_added: "1", files_removed: "0",
    }]} />);
    expect(markup).toContain('aria-description="Route volume');
    expect(markup).toContain("Rows read: 12");
    expect(markup).toContain("Δ +2.01MB");
    expect(markup).toContain("Bytes added: 2,107,384 B");
    expect(markup).toContain("Net bytes: +2,107,384 B");
    expect(markup).not.toContain("runs measured");
    expect(markup).not.toContain("observed subtotal");
  });

  it("puts a long dataflow id outside the label/value columns in its tooltip", () => {
    const option = topDataflowsByWorkloadOption([{ name: "very_long_dataflow_name", dataflow_id: "12345678-1234-1234-1234-123456789012", value: "987654321", count: "42" }]) as Chart;
    const content = chartTooltipContent(option.tooltip.formatter)?.({ dataIndex: 0 });
    expect(content?.title).toBe("very_long_dataflow_name");
    expect(content?.context).toBe("Dataflow ID: 12345678-1234-1234-1234-123456789012");
    expect(content?.metrics?.map(({ label }) => label)).toEqual(["Rows read", "Runs"]);
  });

  it("renders the API total_lakehouse_rows_written field on the Volume page", () => {
    const report = { ...EMPTY_MONITORING_REPORT, volume: { ...EMPTY_MONITORING_REPORT.volume, kpis: { total_rows_read: "420", total_rows_written: "420", total_lakehouse_rows_written: "420" } } };
    const markup = renderToStaticMarkup(<VolumePage report={report} filters={filters} rows={[]} totalRows={0} loading={false} sort={{ sortBy: "dataflow_name", sortDir: "asc" }} onSort={() => {}} limit={100} offset={0} onPageChange={() => {}} onPageSizeChange={() => {}} />);
    expect(markup).toContain("Lakehouse rows written: 420; Coverage unavailable");
    expect(markup).toContain('aria-label="About Rows written"');
  });
});
