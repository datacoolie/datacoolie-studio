import { describe, expect, it } from "vitest";
import {
  focusLineageFitOptions,
  initialLargeGraphFitOptions,
  shouldAutoFitLineage,
  visibleLineageFitOptions,
} from "./viewport";

describe("lineage viewport policy", () => {
  it("auto-fits small and medium graphs only", () => {
    expect(shouldAutoFitLineage(60)).toBe(true);
    expect(shouldAutoFitLineage(61)).toBe(false);
  });

  it("keeps dense graph entry readable while centering the graph", () => {
    expect(initialLargeGraphFitOptions()).toMatchObject({
      padding: 0.18,
      minZoom: 0.42,
      maxZoom: 0.5,
    });
  });

  it("allows explicit fit actions to zoom out farther than the dense entry view", () => {
    expect(visibleLineageFitOptions()).toMatchObject({ minZoom: 0.34, maxZoom: 1 });
    expect(focusLineageFitOptions()).toMatchObject({ minZoom: 0.42, maxZoom: 1 });
  });
});
