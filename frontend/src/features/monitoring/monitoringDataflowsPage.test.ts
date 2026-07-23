import { describe, expect, it } from "vitest";import { lifecycleStatusItems, statusColor } from "./components/monitoringPrimitives";

describe("Dataflows page lifecycle status values", () => {
  it("preserves label order and canonical status colors", () => {
    expect(lifecycleStatusItems(2, 3, 5)).toEqual([
      { status: "skipped", value: 2, color: statusColor("skipped") },
      { status: "running", value: 3, color: statusColor("running") },
      { status: "pending", value: 5, color: statusColor("pending") }
    ]);
  });
});
