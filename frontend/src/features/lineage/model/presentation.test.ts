import { describe, expect, it } from "vitest";
import type { LineageAsset } from "../../../shared/api/types";
import { presentLineageAsset } from "./presentation";

describe("lineage asset presentation", () => {
  it("uses the table as friendly title instead of a format-prefixed path", () => {
    const asset = presentLineageAsset(node({
      label: "parquet/orders_api_wm_iso",
      endpoint_locator: "parquet/orders_api_wm_iso",
      table: "orders_api_wm_iso",
      path: "./output/parquet/orders_api_wm_iso",
      format: "parquet"
    }));

    expect(asset.locator).toBe("orders_api_wm_iso");
    expect(asset.iconKind).toBe("parquet");
    expect(asset.badge).toBe("PARQUET");
    expect(asset.fullIdentity).toContain("./output/parquet/orders_api_wm_iso");
  });

  it("uses semantic icon kinds for supported formats", () => {
    expect(presentLineageAsset(node({ format: "json" })).iconKind).toBe("json");
    expect(presentLineageAsset(node({ format: "excel" })).iconKind).toBe("excel");
    expect(presentLineageAsset(node({ format: "delta" })).iconKind).toBe("delta");
    expect(presentLineageAsset(node({ format: "parquet" })).iconKind).toBe("parquet");
    expect(presentLineageAsset(node({ format: "avro" })).iconKind).toBe("avro");
    expect(presentLineageAsset(node({ format: "yaml" })).iconKind).toBe("yaml");
    expect(presentLineageAsset(node({ format: "api" })).iconKind).toBe("api");
    expect(presentLineageAsset(node({ format: "function" })).iconKind).toBe("code");
  });

  it("uses the final Python function segment as the title", () => {
    const asset = presentLineageAsset(node({
      python_function: "functions.sources.read_orders",
      endpoint_kind: "python"
    }));

    expect(asset.locator).toBe("read_orders");
    expect(asset.iconKind).toBe("python");
  });

  it("gives a SQL query precedence over a configured table placeholder", () => {
    const asset = presentLineageAsset(node({
      kind: "sql_query",
      query: "select * from raw.orders",
      table: "orders",
      endpoint_kind: "sql"
    }));

    expect(asset.locator).toBe("SQL query");
  });
});

function node(overrides: Partial<LineageAsset>): LineageAsset {
  return {
    id: "asset-1",
    label: "orders",
    kind: "path",
    display_name: "orders",
    declaration_status: "declared",
    connection_name: "local_parquet_dest",
    ...overrides
  };
}
