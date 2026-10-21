import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DataflowVolumeCell } from "./components/monitoringPresentation";
import { MaintenanceRunVolumeCell } from "./details/MaintenanceDetails";
import { PerformanceDetailSections } from "./details/PerformanceDetails";
import { VolumeNetBytes } from "./details/VolumeDetails";
import { performancePageTestUtils } from "./pages/PerformancePage";

describe("monitoring metric presentation", () => {
  it("keeps large rows and signed byte differences exact in the shared volume cell", () => {
    const html = renderToStaticMarkup(<DataflowVolumeCell row={{
      source_rows_read: "9007199254740993",
      destination_rows_written: "900719925474099312345678",
      destination_bytes_added: "9007199254740993",
      destination_bytes_removed: "1",
    } as never} />);

    expect(html).toContain("Rows read: 9,007,199,254,740,993");
    expect(html).toContain("Rows written: 900,719,925,474,099,312,345,678");
    expect(html).toContain("Net lakehouse bytes: 9,007,199,254,740,992 B");
  });

  it("keeps Performance workload tooltip metrics exact while chart coordinates may approximate", () => {
    const option = performancePageTestUtils.workloadEfficiencyOption(
      [{
        dataflow_name: "orders",
        dataflow_run_id: "run-1",
        stage: "silver",
        operation_type: "etl",
        duration_seconds: "12.50",
        rows_processed: "9007199254740993",
        rows_read_per_second: "900719925474099312345678",
        destination_bytes_added: "9007199254740993",
        destination_bytes_removed: "1",
        performance_candidate_priority: 1,
      }],
      "linear",
      "etl",
    );
    const tooltip = (option.tooltip as { formatter?: (params: unknown) => string }).formatter?.({ dataIndex: 0 }) ?? "";
    expect(tooltip).toContain("Rows processed: 9,007,199,254,740,993");
    expect(tooltip).toContain("Rows/s: 900,719,925,474,099,312,345,678");
    expect(tooltip).toContain("Duration: 12.5 s");
    expect(tooltip).toContain("Lakehouse bytes added / removed: 9,007,199,254,740,993 B / 1 B");
  });

  it("keeps performance detail and maintenance run metrics on the native transport path", () => {
    const performanceHtml = renderToStaticMarkup(<PerformanceDetailSections row={{
      performance_rows_processed: "9007199254740993",
      performance_rows_per_second: "12.50",
      performance_overhead_ratio: "0.10",
      performance_dominant_phase_ratio: "0.90",
    }} />);
    expect(performanceHtml).toContain("9,007,199,254,740,993");
    expect(performanceHtml).toContain("12.5");

    const maintenanceHtml = renderToStaticMarkup(<MaintenanceRunVolumeCell row={{
      source_rows_read: "9007199254740993",
      destination_rows_written: "12",
      destination_bytes_added: "9007199254740993",
      destination_bytes_removed: "1",
      destination_files_removed: null,
      operation_type: "maintenance",
    }} />);
    expect(maintenanceHtml).toContain("9,007,199,254,740,993");
    expect(maintenanceHtml).toContain("1 B");
    const dataflowHtml = renderToStaticMarkup(<MaintenanceRunVolumeCell row={{
      source_rows_read: "1",
      destination_rows_written: "2",
      destination_bytes_added: "9007199254740993",
      destination_bytes_removed: "1",
      operation_type: "etl",
    }} />);
    expect(dataflowHtml).toContain("Bytes added: 9,007,199,254,740,993 B");
    expect(dataflowHtml).toContain("Bytes removed: 1 B");
    const pairedNetHtml = renderToStaticMarkup(<VolumeNetBytes value="9007199254740992" />);
    expect(pairedNetHtml).toContain("9,007,199,254,740,992 B");
    const unknownNetHtml = renderToStaticMarkup(<VolumeNetBytes value={null} />);
    expect(unknownNetHtml).toContain('aria-description="-"');
    expect(unknownNetHtml).toContain(">-</span>");
  });
});
