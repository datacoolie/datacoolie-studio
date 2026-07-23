import { describe, expect, it } from "vitest";
import { presentReferenceResolution } from "./referenceResolutionPresentation";

describe("reference resolution presentation", () => {
  it("uses the same primary labels for every reference surface", () => {
    expect(presentReferenceResolution("automatic")).toEqual({ state: "automatic", label: "Automatic", detail: null });
    expect(presentReferenceResolution("manual")).toEqual({ state: "manual", label: "Manual", detail: null });
    expect(presentReferenceResolution("unresolved")).toEqual({ state: "unresolved", label: "Unresolved", detail: null });
    expect(presentReferenceResolution({ state: "unresolved", reason: "target_missing" })).toEqual({
      state: "unresolved",
      label: "Unresolved",
      detail: "The saved mapping target is unavailable",
    });
  });
});
