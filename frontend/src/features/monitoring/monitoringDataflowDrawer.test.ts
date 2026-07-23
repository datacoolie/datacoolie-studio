import { describe, expect, it } from "vitest";import { failureDiagnosticTags } from "./details/FailureDetails";
import { formatRuntimeTimestampForDisplay, phaseRuntimeStatusClass } from "./details/detailPrimitives";

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

  it("formats naive runtime End timestamps consistently with explicit UTC Start timestamps", () => {
    expect(formatRuntimeTimestampForDisplay("2026-07-22T01:02:03Z", "Asia/Saigon"))
      .toBe(formatRuntimeTimestampForDisplay("2026-07-22T01:02:03", "Asia/Saigon"));
  });
});

describe("failure drawer diagnostic tags", () => {
  it("normalizes, deduplicates, and bounds drawer-only signals", () => {
    expect(failureDiagnosticTags(["OAuth", "Authentication", "OAuth", " ", "DNS", "HTTP 401", "Token", "Extra"])).toEqual([
      "OAuth", "Authentication", "DNS", "HTTP 401", "Token"
    ]);
  });

  it("hides the section for non-list or empty evidence", () => {
    expect(failureDiagnosticTags(null)).toEqual([]);
    expect(failureDiagnosticTags([])).toEqual([]);
  });
});
