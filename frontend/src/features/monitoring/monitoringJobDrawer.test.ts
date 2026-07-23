import { describe, expect, it } from "vitest";
import { lifecycleStatusPresentations } from "../../shared/statusPresentation";
import { semanticNumber } from "./MonitoringDetailDrawer";

describe("Job drawer semantic status colors", () => {
  it.each([
    ["total_succeeded", "success"],
    ["total_failed", "failed"],
    ["total_skipped", "skipped"],
    ["total_running", "running"],
    ["total_pending", "pending"],
  ])("maps %s counts to %s even when zero", (field, intent) => {
    expect(semanticNumber(field, 0)).toEqual({ kind: "count", value: 0, intent });
  });

  it("keeps pending distinct from skipped in the status presentation SoT", () => {
    expect(lifecycleStatusPresentations.pending.chartColor).not.toBe(lifecycleStatusPresentations.skipped.chartColor);
    expect(lifecycleStatusPresentations.pending.pillBackground).not.toBe(lifecycleStatusPresentations.skipped.pillBackground);
  });
});
