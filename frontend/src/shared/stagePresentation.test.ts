import { describe, expect, it } from "vitest";
import {
  compareStageValues,
  createStageToneMap,
  normalizeStage,
  normalizeStageSummary,
  stageFilterMatches,
  stageToneClass,
  stageToneIndex
} from "./stagePresentation";

describe("stagePresentation", () => {
  it("normalizes exact stage identity without changing the display value", () => {
    expect(normalizeStage("  Silver2 ")).toBe("silver2");
    expect(stageFilterMatches(" Silver2 ", "silver2")).toBe(true);
    expect(stageFilterMatches("silver20", "silver2")).toBe(false);
  });

  it("sorts flow families and natural numeric names", () => {
    expect(["gold", "silver10", "bronze", "silver2", "source", "other"].sort(compareStageValues)).toEqual([
      "source",
      "bronze",
      "silver2",
      "silver10",
      "gold",
      "other"
    ]);
  });

  it("merges case variants and orders Overview counts by stage flow", () => {
    expect(normalizeStageSummary([
      { name: "silver10", count: 2 },
      { name: " Silver2 ", count: 3 },
      { name: "SILVER2", count: 4 },
      { name: "", count: 99 }
    ])).toEqual([
      { name: "Silver2", count: 7 },
      { name: "silver10", count: 2 }
    ]);
  });

  it("maps the same stage to the same tone in every consumer", () => {
    expect(stageToneClass("Silver2")).toBe(stageToneClass(" silver2 "));
    expect(stageToneIndex("silver2")).not.toBeNull();
    expect(stageToneClass(null)).toContain("metadata-stage-tone-neutral");
  });

  it("allocates distinct tones before reusing the finite palette", () => {
    const values = Array.from({ length: 14 }, (_, index) => `stage-${index}`);
    const toneMap = createStageToneMap(values);
    const allocatedTones = values.map((value) => toneMap.get(value));

    expect(new Set(allocatedTones.slice(0, 12)).size).toBe(12);
    expect(allocatedTones.every((tone) => tone != null && tone >= 0 && tone < 12)).toBe(true);
    expect(createStageToneMap([...values].reverse())).toEqual(toneMap);
  });
});
