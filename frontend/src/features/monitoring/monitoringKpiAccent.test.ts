import { describe, expect, it } from "vitest";import { healthCardAccentClass, runtimePhaseContributionTooltip } from "./components/monitoringPrimitives";

describe("Monitoring KPI accent contract", () => {
  it("keeps neutral cards structurally accented", () => {
    expect(healthCardAccentClass("neutral", "neutral")).toBe("health-card-accent-neutral");
  });

  it("resolves dynamic accents from health intent", () => {
    expect(healthCardAccentClass("intent", "good")).toBe("health-card-accent-good");
    expect(healthCardAccentClass("intent", "warning")).toBe("health-card-accent-warning");
    expect(healthCardAccentClass("intent", "bad")).toBe("health-card-accent-bad");
    expect(healthCardAccentClass("intent", "neutral")).toBe("health-card-accent-neutral");
  });

  it("keeps stable metric-family accents independent from health intent", () => {
    expect(healthCardAccentClass("source", "warning")).toBe("health-card-accent-source");
    expect(healthCardAccentClass("transform", "neutral")).toBe("health-card-accent-transform");
    expect(healthCardAccentClass("destination", "bad")).toBe("health-card-accent-destination");
    expect(healthCardAccentClass("storage", "neutral")).toBe("health-card-accent-storage");
    expect(healthCardAccentClass("overhead", "neutral")).toBe("health-card-accent-overhead");
  });

  it("documents the runtime phase contribution population and failure fallback", () => {
    const tooltip = runtimePhaseContributionTooltip("stage");

    expect(tooltip).toContain("exclude pending and running");
    expect(tooltip).toContain("eligible statuses are succeeded, failed, and skipped");
    expect(tooltip).toContain("attributed to Overhead");
  });
});
