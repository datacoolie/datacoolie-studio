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
      "Phase",
      "Status",
      "Duration",
      "Rows",
      "Message",
    ]);
    expect(columns.find((column) => column.label === "Stage / operation")?.sortKey).toBe("stage");
    expect(columns.find((column) => column.label === "Rows")?.sortKey).toBe("source_rows_read");
    expect(columns.find((column) => column.label === "Time")?.key).toBe("start_time");
  });

  it("measures paired child values and keeps Time at its explicit width", () => {
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
    expect(columns.find((column) => column.label === "Time")).toMatchObject({ width: 170 });
  });
});
