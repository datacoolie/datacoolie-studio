import { describe, expect, it } from "vitest";
import { freshnessFilterTransition } from "./monitoringFreshnessFilterGuard";

describe("Freshness filter lifecycle guard", () => {
  it("does not invalidate an unchanged filter signature", () => {
    expect(freshnessFilterTransition("a", "a", "freshness", [])).toEqual({ changed: false, shouldClose: false });
  });

  it("closes an active Freshness chain when the effective filters change", () => {
    expect(freshnessFilterTransition("a", "b", "freshness", [])).toEqual({ changed: true, shouldClose: true });
    expect(freshnessFilterTransition("a", "b", "dataflow", ["freshness"])).toEqual({ changed: true, shouldClose: true });
  });

  it("leaves unrelated drawers open while still reporting a filter change", () => {
    expect(freshnessFilterTransition("a", "b", "job", ["dataflow"])).toEqual({ changed: true, shouldClose: false });
  });
});
