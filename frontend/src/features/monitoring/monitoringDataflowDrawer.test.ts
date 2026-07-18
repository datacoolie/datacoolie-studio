import { describe, expect, it } from "vitest";
import { phaseRuntimeStatusClass } from "./MonitoringDetailDrawer";

describe("dataflow run drawer runtime status", () => {
  it.each([
    ["succeeded", "is-succeeded"],
    ["failed", "is-failed"],
    ["skipped", "is-skipped"],
    ["running", "is-running"],
    ["pending", "is-pending"],
    [null, "is-unknown"],
  ])("maps %s to its runtime card status class", (status, expected) => {
    expect(phaseRuntimeStatusClass(status)).toBe(`monitoring-dataflow-runtime-card ${expected}`);
  });
});
