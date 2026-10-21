import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DataflowRegistryDetailSections } from "./DataflowRegistryDetails";

describe("dataflow registry narrative", () => {
  it("renders a neutral profile, three summary cards, recent statuses and runtime table", () => {
    const html = renderToStaticMarkup(
      <DataflowRegistryDetailSections
        row={{
          dataflow_id: "dataflow-with-a-long-id-that-must-wrap",
          profile_snapshot: {
            dataflow_id: "dataflow-with-a-long-id-that-must-wrap",
            dataflow_run_id: "profile-run-1",
            _source_id: 1,
            event_time: "2026-10-14T08:00:00Z",
            outside_filter: false,
            dataflow_name: "Orders",
            dataflow_description: "Copies source orders into the warehouse.",
            source_name: "Lakehouse",
            source_full_table: "raw.orders",
            destination_name: "Warehouse",
            destination_full_table: "silver.orders",
          },
          run_count: "5",
          succeeded_count: "2", failed_count: "1", skipped_count: "0", running_count: "1", pending_count: "1", other_count: "0",
          volume_rows_read: "100", volume_rows_written: "90",
          volume_rows_inserted: "80", volume_rows_updated: "10", volume_rows_deleted: "0",
          volume_files_added: "2", volume_files_removed: "0", volume_bytes_added: "1024", volume_bytes_removed: "0",
          performance_avg_duration_seconds: 60,
          performance_source_contribution: 20, performance_source_duration_seconds: 40,
          performance_transform_contribution: 30, performance_transform_duration_seconds: 60,
          performance_destination_contribution: 40, performance_destination_duration_seconds: 80,
          performance_overhead_contribution: 10, performance_overhead_duration_seconds: 20,
          recent_runs: [
            { dataflow_id: "df-1", dataflow_run_id: "run-2", _source_id: 1, event_time: "2026-10-14T08:00:00Z", status: "succeeded" },
            { dataflow_id: "df-1", dataflow_run_id: "run-0", _source_id: 1, event_time: null, status: "pending" },
            { dataflow_id: "df-1", dataflow_run_id: "run-1", _source_id: 1, event_time: "2026-10-13T08:00:00Z", status: "failed" },
          ],
        }}
        runtimeRows={[{
          platform_name: "Fabric",
          engine_name: "DuckDB",
          metadata_provider_name: "Unity",
          run_count: "5",
          latest_run_at: "2026-10-14T08:00:00Z",
          latest_run_id: "run-2",
          latest_source_id: 1,
        }]}
        runtimeTotal={1}
        runtimeSort={{ sortBy: "run_count", sortDir: "desc" }}
        runtimeLoading={false}
        executionRows={[]}
        executionTotal={0}
        executionSort={{ sortBy: "event_time", sortDir: "desc" }}
      />,
    );

    expect(html).not.toContain("Profile snapshot");
    expect(html).toContain("Dataflow summary");
    expect(html).toContain("Workload");
    expect(html).toContain("Insight");
    expect(html).toContain("dataflow-with-a-long-id-that-must-wrap");
    expect(html).toContain('class="monitoring-registry-full-id">dataflo-...-wrap</strong>');
    for (const state of ["Succeeded", "Failed", "Skipped", "Running", "Pending"]) {
      expect(html).toContain(`<dt>${state}</dt><dd>`);
    }
    expect(html).not.toContain("<dt>Other</dt>");
    expect(html).toContain("Avg run duration");
    expect(html).not.toContain("Average run");
    expect(html).not.toContain("<dt>Latest check</dt>");
    expect(html).not.toContain("Last recorded save");
    expect(html).toContain("Last watermark");
    expect(html).not.toContain("all retained history");
    expect(html).not.toContain("all history");
    for (const group of ["Rows", "Files", "Bytes"]) expect(html).toContain(`<h5>${group}</h5>`);
    expect(html).toContain('class="dataflow-workload-group"');
    expect(html).toContain('dataflow-insight-phase monitoring-registry-insight-contribution');
    for (const phase of ["source", "transform", "destination", "overhead"]) expect(html).toContain(`data-phase="${phase}"`);
    expect(html).toMatch(/monitoring-registry-recent-status[^>]*aria-description="failed · [^"]*2026-10-13/);
    expect(html).toMatch(/monitoring-registry-recent-status[^>]*aria-description="succeeded · [^"]*2026-10-14/);
    expect(html).toContain("Latest five runs");
    expect(html).toContain("Runtime");
    expect(html).toContain("Metadata provider");
    expect(html).toContain("Execution");
    expect(html).not.toContain("monitoring-registry-message");
    expect(html.indexOf("status-pending")).toBeLessThan(html.indexOf("status-failed"));
    expect(html.indexOf("status-failed")).toBeLessThan(html.indexOf("status-succeeded"));
  });
  it("preserves canonical backend ordering for timestamp ties", () => {
    const html = renderToStaticMarkup(<DataflowRegistryDetailSections row={{
      dataflow_id: "df-1", recent_runs: [
        { dataflow_id: "df-1", dataflow_run_id: "run-z", _source_id: 2, event_time: "2026-10-14T08:00:00Z", status: "failed" },
        { dataflow_id: "df-1", dataflow_run_id: "run-a", _source_id: 1, event_time: "2026-10-14T08:00:00Z", status: "succeeded" },
      ],
    }} />);
    expect(html.indexOf('aria-description="failed ·')).toBeLessThan(html.indexOf('aria-description="succeeded ·'));
  });
});
