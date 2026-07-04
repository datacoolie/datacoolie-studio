import { describe, expect, it } from "vitest";
import type { LineageAsset, LineageResponse } from "../../../shared/api/types";
import { createLineageGraphIndex, searchLineage, selectVisibleLineage } from "./graphIndex";

const lineage: LineageResponse = {
  schema_version: "lineage.v2",
  summary: summary({ assets: 5, references: 1, dataflows: 3, dependencies: 2 }),
  diagnostics: [],
  assets: [
    asset("a", "source", "A", "csv"),
    asset("b", "warehouse", "B", "delta"),
    asset("c", "mart", "C", "parquet"),
    asset("d", "sibling", "D", "json"),
    { ...asset("q", "warehouse", "Query orders", "sql"), kind: "sql_query", query: "select * from B" }
  ],
  references: [{
    id: "r",
    kind: "table_reference",
    display_name: "unknown.orders",
    resolution_status: "unresolved",
    raw_value: "unknown.orders",
    provenance: "sql",
    target_asset_id: "q",
    candidate_asset_ids: [],
    reason_code: "not_found",
    observations: []
  }],
  dataflows: [
    flow("ab", "A to B", "a", "b", "ingest"),
    flow("qc", "Query to C", "q", "c", "model"),
    flow("ad", "A to D", "a", "d", "ingest")
  ],
  dependencies: [
    dependency("bq", "asset", "b", "q", "resolved"),
    dependency("rq", "reference", "r", "q", "unresolved")
  ]
};

const noFilters = { connections: [], stages: [], formats: [], resolutions: [] };

describe("typed lineage traces", () => {
  const index = createLineageGraphIndex(lineage);

  it("keeps dataflow and dependency ancestors without sibling branches", () => {
    const visible = selectVisibleLineage(index, noFilters, [{ kind: "asset", id: "b" }], "both", false);
    expect(visible.entities.map((entity) => entity.id).sort()).toEqual(["a", "b", "c", "q"]);
    expect(visible.dataflows.map((item) => item.id).sort()).toEqual(["ab", "qc"]);
    expect(visible.dependencies.map((item) => item.id).sort()).toEqual(["bq"]);
  });

  it("respects direction across computational dependencies", () => {
    const upstream = selectVisibleLineage(index, noFilters, [{ kind: "asset", id: "q" }], "upstream", false);
    const downstream = selectVisibleLineage(index, noFilters, [{ kind: "asset", id: "q" }], "downstream", false);
    expect(upstream.dataflows.map((item) => item.id)).toEqual(["ab"]);
    expect(upstream.dependencies.map((item) => item.id).sort()).toEqual(["bq", "rq"]);
    expect(upstream.entities.map((item) => item.id)).toContain("r");
    expect(downstream.dataflows.map((item) => item.id)).toEqual(["qc"]);
  });

  it("hides unresolved references in the full graph unless requested", () => {
    const compact = selectVisibleLineage(index, noFilters, [], "both", false);
    const expanded = selectVisibleLineage(index, noFilters, [], "both", true);
    expect(compact.entities.map((item) => item.id)).not.toContain("r");
    expect(compact.dependencies.map((item) => item.id)).toEqual(["bq"]);
    expect(compact.issueCountByAsset.get("q")).toBe(1);
    expect(expanded.entities.map((item) => item.id)).toContain("r");
    expect(expanded.dependencies.map((item) => item.id).sort()).toEqual(["bq", "rq"]);
  });

  it("applies filters before tracing", () => {
    const visible = selectVisibleLineage(
      index,
      { ...noFilters, stages: ["model"] },
      [{ kind: "asset", id: "q" }],
      "both",
      false
    );
    expect(visible.dataflows.map((item) => item.id)).toEqual(["qc"]);
    expect(visible.dependencies).toEqual([]);
  });

  it("traces from a selected dataflow edge", () => {
    const visible = selectVisibleLineage(index, noFilters, [{ kind: "dataflow", id: "ab" }], "both", false);
    expect(visible.dataflows.map((item) => item.id).sort()).toEqual(["ab", "qc"]);
    expect(visible.dependencies.map((item) => item.id).sort()).toEqual(["bq"]);
  });

  it("unions traces from multiple focus items", () => {
    const visible = selectVisibleLineage(
      index,
      noFilters,
      [{ kind: "asset", id: "b" }, { kind: "asset", id: "d" }],
      "both",
      false
    );
    expect(visible.entities.map((item) => item.id).sort()).toEqual(["a", "b", "c", "d", "q"]);
    expect(visible.dataflows.map((item) => item.id).sort()).toEqual(["ab", "ad", "qc"]);
  });
});

describe("lineage search", () => {
  const index = createLineageGraphIndex(lineage);

  it("matches canonical identities before friendly titles", () => {
    expect(searchLineage(index, "ab")[0]).toMatchObject({
      id: "ab",
      kind: "dataflow",
      identity: "ab"
    });
  });

  it("matches multiple terms across connection and asset fields", () => {
    expect(searchLineage(index, "source A")[0]).toMatchObject({
      id: "a",
      kind: "asset"
    });
  });
});

function asset(id: string, connection: string, locator: string, format: string): LineageAsset {
  return {
    id,
    label: locator,
    kind: "path",
    display_name: locator,
    declaration_status: "declared",
    display_label: locator,
    endpoint_locator: locator,
    endpoint_kind: "file",
    identity_type: "physical_path",
    connection_name: connection,
    format,
    path: `./${locator}`
  };
}

function flow(id: string, name: string, source: string, target: string, stage: string) {
  return {
    id,
    dataflow_id: id,
    name,
    source_asset_id: source,
    destination_asset_id: target,
    stage,
    load_type: null,
    metadata_source_id: 1,
    metadata_source_uri: "metadata.json"
  };
}

function dependency(
  id: string,
  entityType: "asset" | "reference",
  source: string,
  target: string,
  status: "resolved" | "unresolved"
) {
  return {
    id,
    source: { entity_type: entityType, id: source },
    target_asset_id: target,
    kind: "reads" as const,
    provenance: "sql" as const,
    resolution_status: status,
    resolution_method: "test",
    observations: []
  };
}

function summary(overrides: Partial<LineageResponse["summary"]>): LineageResponse["summary"] {
  return {
    assets: 0,
    references: 0,
    dataflows: 0,
    dependencies: 0,
    stitched_assets: 0,
    declared_assets: 0,
    discovered_only_assets: 0,
    resolved_dependencies: 0,
    discovered_only_dependencies: 0,
    ambiguous_dependencies: 0,
    unresolved_dependencies: 0,
    diagnostics: 0,
    ...overrides
  };
}
