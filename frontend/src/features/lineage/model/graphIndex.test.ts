import { describe, expect, it } from "vitest";
import type { LineageAsset, LineageResponse } from "../../../shared/api/domainTypes";
import { createLineageGraphIndex, findLineageDataflowByMetadataIdentity, groupRelationsByNeighbor, referenceNeighborAttentionStatus, searchLineage, selectVisibleLineage } from "./graphIndex";

const lineage: LineageResponse = {
  schema_version: "lineage.v4",
  summary: summary({ assets: 5, references: 2, dataflows: 3, dependencies: 2 }),
  assets: [
    asset("a", "source", "A", "csv"),
    asset("b", "warehouse", "B", "delta"),
    asset("c", "mart", "C", "parquet"),
    asset("d", "sibling", "D", "json"),
    { ...asset("q", "warehouse", "Query orders", "sql"), asset_type: "sql_query", query: "select * from B" }
  ],
  references: [
    {
      id: "r",
      entity_type: "reference",
      reference_type: "table_reference",
      display_name: "unknown.orders",
      normalized_value: "unknown.orders",
      resolution: { state: "unresolved", reason: "no_match" },
      resolved_asset_id: null,
      resolved_asset_ids: [],
      candidate_asset_ids: [],
      occurrence_count: 1,
      consumer_asset_ids: ["q"],
      provenances: ["sql"],
      dependency_count: 1,
    },
    {
      id: "reference:bq",
      entity_type: "reference",
      reference_type: "table_reference",
      display_name: "warehouse.B",
      normalized_value: "warehouse.b",
      resolution: { state: "automatic" },
      resolved_asset_id: "b",
      resolved_asset_ids: ["b"],
      candidate_asset_ids: ["b"],
      occurrence_count: 1,
      consumer_asset_ids: ["q"],
      provenances: ["sql"],
      dependency_count: 1,
    }
  ],
  dataflows: [
    flow("ab", "A to B", "a", "b", "ingest"),
    flow("qc", "Query to C", "q", "c", "model"),
    flow("ad", "A to D", "a", "d", "ingest")
  ],
  dependencies: [
    dependency("bq", "b", "reference:bq", "q", "automatic"),
    dependency("rq", null, "r", "q", "unresolved")
  ]
};

const noFilters = { connections: [], stages: [], formats: [], resolutions: [] };

describe("typed lineage traces", () => {
  const index = createLineageGraphIndex(lineage);

  it("reuses the derived index for the same immutable graph response", () => {
    expect(createLineageGraphIndex(lineage)).toBe(index);
  });

  it("resolves a dataflow focus by metadata source and dataflow identity", () => {
    expect(findLineageDataflowByMetadataIdentity(index, {
      metadataSourceId: 1,
      dataflowId: "ab",
      name: "wrong fallback",
    })?.id).toBe("ab");
  });

  it("does not guess an ambiguous dataflow name", () => {
    const ambiguousIndex = createLineageGraphIndex({
      ...lineage,
      dataflows: [...lineage.dataflows, { ...lineage.dataflows[0], id: "ab-copy" }],
    });
    expect(findLineageDataflowByMetadataIdentity(ambiguousIndex, {
      metadataSourceId: 1,
      name: "A to B",
    })).toBeNull();
  });

  it("groups parallel relations beneath one neighboring entity", () => {
    const relation = index.relations.find((item) => item.id === "ab")!;
    const groups = groupRelationsByNeighbor([relation, { ...relation, id: "ab-copy" }], "source");
    expect(groups).toHaveLength(1);
    expect(groups[0].entityId).toBe("a");
    expect(groups[0].relations.map((item) => item.id)).toEqual(["ab", "ab-copy"]);
  });

  it("shows dependency attention on a reference neighbor but not its consumer asset", () => {
    const relation = index.relations.find((item) => item.id === "rq")!;

    expect(referenceNeighborAttentionStatus(index.entityById.get("r"), [relation])).toBe("unresolved");
    expect(referenceNeighborAttentionStatus(index.entityById.get("q"), [relation])).toBeNull();
  });

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

  it("traces a resolved reference through its resolved asset instead of isolating it", () => {
    const visible = selectVisibleLineage(
      index,
      noFilters,
      [{ kind: "reference", id: "reference:bq" }],
      "both",
      false
    );

    expect(visible.entities.map((item) => item.id).sort()).toEqual(["a", "b", "c", "q", "reference:bq"]);
    expect(visible.dataflows.map((item) => item.id).sort()).toEqual(["ab", "qc"]);
    expect(visible.dependencies.map((item) => item.id)).toEqual(["bq"]);
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

  it("counts repeated attention dependencies for one canonical reference once", () => {
    const repeatedReference: LineageResponse = {
      ...lineage,
      dependencies: [
        ...lineage.dependencies,
        { ...lineage.dependencies.find((item) => item.id === "rq")!, id: "rq-repeat", reference_occurrence_id: "ro-repeat" }
      ]
    };
    const repeatedIndex = createLineageGraphIndex(repeatedReference);
    const visible = selectVisibleLineage(repeatedIndex, noFilters, [], "both", false);

    expect(visible.issueCountByAsset.get("q")).toBe(1);
  });

  it("uses filtered relations as seeds and preserves directional context", () => {
    const manualLineage: LineageResponse = {
      ...lineage,
      references: lineage.references.map((reference) => reference.id === "reference:bq"
        ? { ...reference, resolution: { state: "manual" } }
        : reference),
      dependencies: lineage.dependencies.map((item) => item.id === "bq"
        ? { ...item, resolution: { state: "manual" } }
        : item),
    };
    const manualIndex = createLineageGraphIndex(manualLineage);
    const filters = { ...noFilters, resolutions: ["manual"] };

    const upstream = selectVisibleLineage(manualIndex, filters, [], "upstream", false);
    expect(upstream.dataflows.map((item) => item.id)).toEqual(["ab"]);
    expect(upstream.dependencies.map((item) => item.id)).toEqual(["bq"]);

    const downstream = selectVisibleLineage(manualIndex, filters, [], "downstream", false);
    expect(downstream.dataflows.map((item) => item.id)).toEqual(["qc"]);
    expect(downstream.dependencies.map((item) => item.id)).toEqual(["bq"]);

    const both = selectVisibleLineage(manualIndex, filters, [], "both", false);
    expect(both.dataflows.map((item) => item.id).sort()).toEqual(["ab", "qc"]);
    expect(both.dependencies.map((item) => item.id)).toEqual(["bq"]);
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
    entity_type: "asset",
    label: locator,
    asset_type: "path",
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
  resolvedAssetId: string | null,
  referenceId: string,
  target: string,
  state: "automatic" | "unresolved"
) {
  return {
    id,
    target_asset_id: target,
    consumer_asset_id: target,
    kind: "reads" as const,
    provenance: "sql" as const,
    resolution: { state, reason: state === "unresolved" ? "no_match" as const : undefined },
    resolution_method: "test",
    reference_id: referenceId,
    reference_occurrence_id: referenceId === "r" ? "ro" : "reference-occurrence:bq",
    resolved_asset_id: resolvedAssetId,
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
    automatic_references: 0,
    manual_references: 0,
    unresolved_references: 0,
    automatic_dependencies: 0,
    manual_dependencies: 0,
    unresolved_dependencies: 0,
    diagnostics: 0,
    ...overrides
  };
}
