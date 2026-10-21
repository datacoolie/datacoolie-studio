import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { MonitoringRecord } from "../../../shared/api/domainTypes";
import { VolumeRegistryTable } from "./VolumeRegistryTable";

describe("Dataflow volume registry tooltips", () => {
  it("keeps run, workload, byte, and signal summaries structured and concise", () => {
    const row = {
      dataflow_id: "df-1",
      dataflow_name: "orders_load",
      stage: "load",
      source_name: "source",
      destination_name: "destination",
      run_count: "3",
      succeeded_count: "2",
      failed_count: "0",
      skipped_count: "1",
      running_count: "0",
      pending_count: "0",
      other_count: "0",
      volume_rows_read: "100",
      volume_rows_read_observed: "100",
      volume_rows_read_measured: "2",
      volume_rows_read_eligible: "3",
      volume_rows_read_avg_per_run: "50",
      volume_rows_written: "80",
      volume_rows_written_observed: "80",
      volume_rows_written_measured: "2",
      volume_rows_written_eligible: "3",
      volume_rows_written_avg_per_run: "40",
      volume_files_added: "6",
      volume_files_added_observed: "6",
      volume_files_added_measured: "2",
      volume_files_added_eligible: "3",
      volume_files_added_avg_per_run: "3",
      volume_files_removed: "1",
      volume_files_removed_observed: "1",
      volume_files_removed_measured: "1",
      volume_files_removed_eligible: "3",
      volume_files_removed_avg_per_run: "1",
      volume_bytes_added: "2048",
      volume_bytes_added_observed: "2048",
      volume_bytes_added_measured: "2",
      volume_bytes_added_eligible: "3",
      volume_bytes_removed: "1024",
      volume_bytes_removed_observed: "1024",
      volume_bytes_removed_measured: "1",
      volume_bytes_removed_eligible: "3",
      volume_net_bytes: "512",
      volume_net_bytes_observed: "512",
      volume_net_bytes_measured: "1",
      volume_net_bytes_eligible: "3",
      latest_run_id: "run-123",
      latest_run_at: "2026-10-20T11:32:59Z",
      latest_run_status: "succeeded",
      volume_candidate_signals: [
        { kind: "read", label: "High rows read", value: "100", threshold: "25", ratio: "4" },
      ],
      volume_candidate_run_count: "1",
      volume_candidate_run_reasons: ["High rows read"],
    } as unknown as MonitoringRecord;
    const html = renderToStaticMarkup(<VolumeRegistryTable rows={[row]} />);

    expect(html).toContain("Filtered runs");
    expect(html).toContain("Succeeded: 2");
    expect(html).toContain("Rows read");
    expect(html).toContain("Average per run: 50 r/run");
    expect(html).toContain("Coverage: 2 / 3 runs");
    expect(html).toContain("Added: 2,048 B · 2 / 3 runs");
    expect(html).toContain("Paired net: +512 B · 1 / 3 runs");
    expect(html).toContain("Byte signal is the absolute value of independent added total minus independent removed total.");
    expect(html).toContain("High rows read: 100 · P95 25 · 4×");
    expect(html).not.toContain("All filtered canonical runs");
    expect(html).not.toContain("Total: 100 r; 2 / 3 runs measured");
    const bytesDescription = [...html.matchAll(/aria-description="([^"]*)"/gu)]
      .map(([, description]) => description)
      .find((description) => description.startsWith("Bytes\n"));
    expect(bytesDescription).toContain("Paired net: +512 B · 1 / 3 runs");
    expect(bytesDescription).not.toContain("Total in current filters");
    expect(bytesDescription).not.toContain("runs measured");
  });
});
