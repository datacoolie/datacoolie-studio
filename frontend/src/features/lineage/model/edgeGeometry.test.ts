import { describe, expect, it } from "vitest";
import {
  estimateLabelWidth,
  labelAnchorX,
  labelTransform,
  recommendedLayerSpacing,
  resolveLabelPlacement,
  resolveLabelSegment
} from "./edgeGeometry";

describe("dataflow label segment placement", () => {
  it("honors fan-out and fan-in segment preferences", () => {
    expect(resolveLabelSegment("target", 180, 70)).toBe("target");
    expect(resolveLabelSegment("source", 70, 180)).toBe("source");
  });

  it("uses the longest horizontal segment with a deterministic source tie-break", () => {
    expect(resolveLabelSegment("longest", 180, 70)).toBe("source");
    expect(resolveLabelSegment("longest", 70, 180)).toBe("target");
    expect(resolveLabelSegment("longest", 100, 100)).toBe("source");
  });

  it("anchors fan-out labels toward the destination while allowing text to expand left", () => {
    expect(resolveLabelPlacement("target", 180, 70)).toEqual({
      segment: "target",
      alignment: "end"
    });
    expect(labelTransform("end", 240, 80)).toContain("translate(-100%, -100%)");
    expect(labelAnchorX("end", 100, 400)).toBe(388);
  });

  it("anchors fan-in labels toward the source while allowing text to expand right", () => {
    expect(resolveLabelPlacement("source", 70, 180)).toEqual({
      segment: "source",
      alignment: "start"
    });
    expect(labelTransform("start", 120, 80)).toContain("translate(0%, -100%)");
    expect(labelAnchorX("start", 100, 400)).toBe(112);
  });

  it("sizes labels from their content and caps exceptionally long values", () => {
    expect(estimateLabelWidth("short")).toBeLessThan(estimateLabelWidth("a much longer dataflow name"));
    expect(estimateLabelWidth("x".repeat(200))).toBe(320);
  });

  it("uses visible label distribution to recommend readable inter-layer spacing", () => {
    expect(recommendedLayerSpacing([])).toBe(150);
    expect(recommendedLayerSpacing(["short", "a much longer dataflow label"])).toBeGreaterThanOrEqual(150);
    expect(recommendedLayerSpacing(["x".repeat(200)])).toBe(368);
  });

});
