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

  it("recognizes embedded stage tokens and lets the first token win", () => {
    expect(connectionStageFamily("wwi_bronze_orders")).toBe("bronze");
    expect(connectionStageFamily("wwi_bronze_orders_silver_summary")).toBe("bronze");
    expect(connectionStageFamily("wwi_silver_orders_bronze_archive")).toBe("silver");
    expect(connectionStageFamily("WWI-GOLD-ORDERS")).toBe("gold");
    expect(connectionStageFamily("silver10_delta")).toBe("silver");
  });

  it("requires stage boundaries instead of matching arbitrary substrings", () => {
    expect(connectionStageFamily("bronzed_table")).toBeNull();
    expect(connectionStageFamily("external_resource")).toBeNull();
    expect(connectionStageFamily("xbronze_orders")).toBeNull();
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
