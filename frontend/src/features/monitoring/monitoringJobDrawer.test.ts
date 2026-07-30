import { describe, expect, it } from "vitest";
import {
  childDataflowRowLines,
  childDataflowStageOperationLines,
  jobChildDataflowColumns,
} from "./MonitoringDetailDrawer";

describe("Job drawer child dataflow table", () => {
  it("uses the compact merged column layout with real sort keys", () => {
    const columns = jobChildDataflowColumns("UTC");

    expect(columns.map((column) => column.label)).toEqual([
      "Dataflow",
      "Stage / operation",
      "Time",
      "Status",
      "Duration",
      "Rows",
      "Issue",
    ]);
    expect(columns.find((column) => column.label === "Stage / operation")?.sortKey).toBe("stage");
    expect(columns.find((column) => column.label === "Rows")?.sortKey).toBe("source_rows_read");
    expect(columns.find((column) => column.label === "Time")?.key).toBe("start_time");
  });

  it("measures every displayed line for paired child dataflow fields", () => {
    const row = {
      stage: "silver",
      operation_type: "merge",
      source_rows_read: 12_345,
      destination_rows_written: 12_000,
      start_time: "2026-07-14T07:19:22Z",
      end_time: "2026-07-14T07:19:24Z",
    };
    const columns = jobChildDataflowColumns("UTC");

    expect(childDataflowStageOperationLines(row)).toEqual(["silver", "merge"]);
    expect(childDataflowRowLines(row)).toEqual(["12,345 read", "12,000 written"]);
    expect(columns.find((column) => column.label === "Time")?.measureValue?.(row, "UTC")).toEqual([
      "2026-07-14 07:19:22 UTC",
      "→ 2026-07-14 07:19:24 UTC",
    ]);
  });
});
