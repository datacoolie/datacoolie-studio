import { describe, expect, it } from "vitest";
import { toggleFilterValue } from "./filterSelection";

describe("lineage filter selection", () => {
  it("selects an option and removes it when clicked again", () => {
    expect(toggleFilterValue([], "manual", false)).toEqual(["manual"]);
    expect(toggleFilterValue(["manual"], "manual", false)).toEqual([]);
  });

  it("replaces another option unless additive selection is requested", () => {
    expect(toggleFilterValue(["manual"], "automatic", false)).toEqual(["automatic"]);
    expect(toggleFilterValue(["manual"], "automatic", true)).toEqual(["manual", "automatic"]);
  });
});
