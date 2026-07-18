import { describe, expect, it } from "vitest";
import { presentReferenceResolution } from "./referenceResolutionPresentation";

describe("reference resolution presentation", () => {
  it("uses the same primary labels for every reference surface", () => {
    expect(presentReferenceResolution("automatic")).toEqual({ state: "automatic", label: "Automatic" });
    expect(presentReferenceResolution("manual")).toEqual({ state: "manual", label: "Manual" });
    expect(presentReferenceResolution("needs_mapping")).toEqual({ state: "needs_mapping", label: "Needs mapping" });
    expect(presentReferenceResolution("review")).toEqual({ state: "review", label: "Review" });
    expect(presentReferenceResolution("missing_target")).toEqual({ state: "missing_target", label: "Target missing" });
  });
});
