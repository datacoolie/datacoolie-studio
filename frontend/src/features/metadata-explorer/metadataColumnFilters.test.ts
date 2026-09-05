import { describe, expect, it } from "vitest";
import {
  metadataColumnFilterMatches,
  metadataColumnFilterOptions,
  metadataColumnFilterSelectionIsComplete,
  metadataColumnFilterValueKey,
  metadataFilterOptionsForRender,
  METADATA_FILTER_BLANK_KEY
} from "./metadataColumnFilters";

describe("metadata column filters", () => {
  const rows = [
    { stage: " Silver2 ", region: "America", configure: { b: 2, a: 1 } },
    { stage: "silver2", region: "America", configure: { a: 1, b: 2 } },
    { stage: "gold", region: "Japan", configure: null },
    { stage: "", region: null, configure: null }
  ];

  it("groups exact values and keeps blanks as a selectable value", () => {
    const result = metadataColumnFilterOptions(rows, "stage");

    expect(result.options.map((option) => option.label)).toEqual(["Blanks", "gold", "Silver2"]);
    expect(result.options.find((option) => option.label === "Silver2")?.count).toBe(2);
    expect(result.options.find((option) => option.isBlank)?.key).toBe(METADATA_FILTER_BLANK_KEY);
  });

  it("uses stable structured values and normalized stage keys", () => {
    expect(metadataColumnFilterValueKey(" Silver2 ", "stage")).toBe(metadataColumnFilterValueKey("silver2", "stage"));
    expect(metadataColumnFilterValueKey(rows[0].configure, "configure")).toBe(
      metadataColumnFilterValueKey(rows[1].configure, "configure")
    );
  });

  it("combines selected values with OR within a column and AND across columns", () => {
    const filters = {
      stage: [metadataColumnFilterValueKey("silver2", "stage")],
      region: [metadataColumnFilterValueKey("America", "region")]
    };

    expect(metadataColumnFilterMatches(rows[0], filters)).toBe(true);
    expect(metadataColumnFilterMatches(rows[1], filters)).toBe(true);
    expect(metadataColumnFilterMatches(rows[2], filters)).toBe(false);
    expect(metadataColumnFilterMatches(rows[0], filters, "stage")).toBe(true);
  });

  it("recognizes a select-all state from the available options", () => {
    const result = metadataColumnFilterOptions(rows, "region");
    expect(metadataColumnFilterSelectionIsComplete(result.options.map((option) => option.key), result.options)).toBe(true);
    expect(metadataColumnFilterSelectionIsComplete([], result.options)).toBe(false);
  });

  it("uses readable labels for boolean values", () => {
    const result = metadataColumnFilterOptions([{ enabled: true }, { enabled: false }], "enabled");
    expect(result.options.map((option) => option.label)).toEqual(["False", "True"]);
  });

  it("bounds high-cardinality option rendering", () => {
    const options = metadataColumnFilterOptions(
      Array.from({ length: 301 }, (_, index) => ({ value: `value-${index}` })),
      "value"
    ).options;
    const rendered = metadataFilterOptionsForRender(options, "");

    expect(rendered.visibleOptions).toHaveLength(300);
    expect(rendered.truncated).toBe(true);
  });
});
