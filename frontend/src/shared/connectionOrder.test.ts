import { describe, expect, it } from "vitest";
import {
  CONNECTION_STAGE_FAMILIES,
  compareConnectionNames,
  connectionStageFamily,
  connectionStageRank,
} from "./connectionOrder";

describe("connectionOrder", () => {
  it("uses the shared source-to-gold family order and puts other connections last", () => {
    expect([
      "analytics",
      "gold_warehouse",
      "silver10_delta",
      "bronze_parquet",
      "source_api",
      "silver2_delta",
    ].sort(compareConnectionNames)).toEqual([
      "source_api",
      "bronze_parquet",
      "silver2_delta",
      "silver10_delta",
      "gold_warehouse",
      "analytics",
    ]);
  });

  it("keeps non-convention connections in Other", () => {
    expect(connectionStageFamily("external_database")).toBeNull();
    expect(connectionStageRank("external_database")).toBe(CONNECTION_STAGE_FAMILIES.length);
  });

  it("sorts connections naturally within a family and keeps blanks last", () => {
    expect(["bronze10", "bronze2", "bronze"].sort(compareConnectionNames)).toEqual([
      "bronze",
      "bronze2",
      "bronze10",
    ]);
    expect(["silver", "", "silver2"].sort(compareConnectionNames)).toEqual(["silver", "silver2", ""]);
  });
});
