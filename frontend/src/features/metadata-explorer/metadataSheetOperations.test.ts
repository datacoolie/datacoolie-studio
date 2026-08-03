import { describe, expect, it } from "vitest";
import { structuredCellKind } from "./metadataSheetOperations";

describe("structuredCellKind", () => {
  it("classifies expanded transform metadata before values are entered", () => {
    for (const columnKey of [
      "transform_select_columns",
      "transform_drop_columns",
      "transform_value_rules",
      "transform_hash_columns",
      "transform_masking_rules"
    ]) {
      expect(structuredCellKind(columnKey, null)).toBe("array");
    }

    expect(structuredCellKind("transform_rename_columns", null)).toBe("object");
  });
});
