import { describe, expect, it } from "vitest";
import { MAX_VISIBLE_TIMEZONE_OPTIONS, matchTimezoneOptions, type TimezoneOption } from "./timezoneOptions";

function option(index: number): TimezoneOption {
  return {
    name: `Zone/${index}`,
    offsetLabel: "UTC+00:00",
    offsetMinutes: 0,
    searchText: `zone/${index}utc+00:00utc+0`,
  };
}

describe("timezone option matching", () => {
  it("bounds the rendered option collection", () => {
    const result = matchTimezoneOptions(Array.from({ length: 80 }, (_, index) => option(index)), "");
    expect(result.total).toBe(80);
    expect(result.visible).toHaveLength(MAX_VISIBLE_TIMEZONE_OPTIONS);
  });

  it("searches the full collection before applying the result bound", () => {
    const result = matchTimezoneOptions([option(1), option(20)], "zone/20");
    expect(result.total).toBe(1);
    expect(result.visible[0]?.name).toBe("Zone/20");
  });

  it("centers the selected timezone in the initial bounded window", () => {
    const options = Array.from({ length: 100 }, (_, index) => option(index));
    const result = matchTimezoneOptions(options, "", "Zone/70");
    expect(result.visible).toHaveLength(MAX_VISIBLE_TIMEZONE_OPTIONS);
    expect(result.visible[25]?.name).toBe("Zone/70");
  });

  it("centers the equivalent offset when a server timezone label is not IANA", () => {
    const options = Array.from({ length: 100 }, (_, index) => ({
      ...option(index),
      offsetMinutes: index < 60 ? 0 : 420,
    }));
    const result = matchTimezoneOptions(options, "", "SE Asia Standard Time", 420);
    expect(result.focusedIndex).toBe(25);
    expect(result.visible[result.focusedIndex]?.offsetMinutes).toBe(420);
  });
});
