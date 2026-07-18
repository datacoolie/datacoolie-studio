import { describe, expect, it } from "vitest";
import type { LatestStatusResponse, LineageDataflow, LineageDependency, LineageReference } from "../../../shared/api/types";
import { dependencyEdgePath } from "../components/LineageEdges";
import { buildLineageFlow, latestRun } from "./flow";
import type { VisibleLineage } from "./types";

describe("lineage runtime evidence matching", () => {
  it("prefers exact dataflow ID over a same-name fallback", () => {
    const status: LatestStatusResponse = {
      latest_by_id: { "flow-2": { status: "failed" } },
      latest_by_name: { duplicate: { status: "succeeded" } },
      ambiguous_names: ["duplicate"],
      errors: []
    };
    expect(latestRun(status, "flow-2", "duplicate")?.status).toBe("failed");
  });

  it("does not use a name fallback when structured matching marks a name ambiguous", () => {
    const status: LatestStatusResponse = {
      latest_by_id: {},
      latest_by_name: {},
      ambiguous_names: ["duplicate"],
      errors: []
    };
    expect(latestRun(status, "flow-2", "duplicate")).toBeNull();
  });
});

describe("lineage fan routing", () => {
  it("does not create sibling-specific route offsets for parallel dataflows", () => {
    const dataflows: LineageDataflow[] = [
      dataflow("flow-a"),
      dataflow("flow-b")
    ];
    const visible: VisibleLineage = {
      entities: [],
      dataflows,
      dependencies: [],
      focusNodeIds: new Set(),
      focusEdgeIds: new Set(),
      issueCountByAsset: new Map(),
      filtersActive: false,
      traceActive: true
    };

    const flow = buildLineageFlow(visible, null, false, null, null);

    expect(flow.edges).toHaveLength(2);
    expect(flow.edges.map((edge) => edge.data)).toEqual([
      expect.not.objectContaining({ laneOffset: expect.anything() }),
      expect.not.objectContaining({ laneOffset: expect.anything() })
    ]);
  });
});

describe("lineage reference nodes", () => {
  it("carries the reference type and status for the reference-specific node treatment", () => {
    const visible: VisibleLineage = {
      entities: [reference("reference-orders", ["sql", "python"])],
      dataflows: [],
      dependencies: [],
      focusNodeIds: new Set(),
      focusEdgeIds: new Set(),
      issueCountByAsset: new Map(),
      filtersActive: false,
      traceActive: false,
    };

    const flow = buildLineageFlow(visible, null, false, null, null);

    expect(flow.nodes[0].data).toMatchObject({
      entityType: "reference",
      connection: "table reference · SQL + Python",
      referenceType: "table_reference",
      referenceStatus: "unresolved",
    });
  });

  it("keeps parallel dependency arrows neutral and assigns them lanes outside a matching dataflow", () => {
    const visible: VisibleLineage = {
      entities: [],
      dataflows: [dataflow("flow-source-target", "source", "target")],
      dependencies: [
        dependency("unresolved", "source"),
        dependency("ambiguous", "source"),
        dependency("mapping_target_missing", "source"),
      ],
      focusNodeIds: new Set(),
      focusEdgeIds: new Set(),
      issueCountByAsset: new Map(),
      filtersActive: false,
      traceActive: false,
    };

    const flow = buildLineageFlow(visible, null, false, null, null);

    expect(flow.edges.map((edge) => edge.style?.stroke)).toEqual([
      "var(--lineage-edge-neutral)",
      "var(--lineage-edge-neutral)",
      "var(--lineage-edge-neutral)",
      "var(--lineage-edge-neutral)",
    ]);
    expect(flow.edges.slice(0, 3).map((edge) => edge.data?.routeLane).sort()).toEqual([-1, 1, 2]);
  });

  it("uses distinct attachment lanes for dotted dependency paths", () => {
    const upwardLane = dependencyEdgePath({ sourceX: 0, sourceY: 50, targetX: 200, targetY: 50, routeLane: -1 });
    const downwardLane = dependencyEdgePath({ sourceX: 0, sourceY: 50, targetX: 200, targetY: 50, routeLane: 1 });

    expect(upwardLane).toContain("V 41 H");
    expect(downwardLane).toContain("V 59 H");
    expect(upwardLane).not.toBe(downwardLane);
  });

});

describe("lineage selection neighborhood", () => {
  it("distinguishes a selected entity from its direct inputs, outputs, and unrelated objects", () => {
    const visible: VisibleLineage = {
      entities: [reference("source"), reference("center"), reference("target"), reference("isolated")],
      dataflows: [
        dataflow("incoming", "source", "center"),
        dataflow("outgoing", "center", "target")
      ],
      dependencies: [],
      focusNodeIds: new Set(),
      focusEdgeIds: new Set(),
      issueCountByAsset: new Map(),
      filtersActive: false,
      traceActive: false
    };

    const flow = buildLineageFlow(visible, null, false, { kind: "reference", id: "center" }, null);
    const nodes = new Map(flow.nodes.map((node) => [node.id, node]));
    const edges = new Map(flow.edges.map((edge) => [edge.id, edge]));

    expect(nodes.get("center")?.data.selectionState).toBe("selected");
    expect(nodes.get("source")?.data.selectionState).toBe("input");
    expect(nodes.get("target")?.data.selectionState).toBe("output");
    expect(nodes.get("isolated")?.data.selectionState).toBe("none");
    expect(edges.get("incoming")?.data?.selectionState).toBe("input");
    expect(edges.get("outgoing")?.data?.selectionState).toBe("output");
  });

  it("keeps a selected relation dominant while marking only its source and target", () => {
    const visible: VisibleLineage = {
      entities: [reference("source"), reference("center"), reference("target")],
      dataflows: [
        dataflow("incoming", "source", "center"),
        dataflow("outgoing", "center", "target")
      ],
      dependencies: [],
      focusNodeIds: new Set(),
      focusEdgeIds: new Set(),
      issueCountByAsset: new Map(),
      filtersActive: false,
      traceActive: false
    };

    const flow = buildLineageFlow(visible, null, false, { kind: "dataflow", id: "outgoing" }, null);
    const nodes = new Map(flow.nodes.map((node) => [node.id, node]));
    const edges = new Map(flow.edges.map((edge) => [edge.id, edge]));

    expect(edges.get("outgoing")?.data?.selectionState).toBe("selected");
    expect(edges.get("outgoing")?.style?.stroke).toBe("var(--lineage-selection-selected)");
    expect(edges.get("incoming")?.data?.selectionState).toBe("none");
    expect(nodes.get("center")?.data.selectionState).toBe("input");
    expect(nodes.get("target")?.data.selectionState).toBe("output");
    expect(nodes.get("source")?.data.selectionState).toBe("none");
  });
});

function dataflow(id: string, sourceAssetId = "source", destinationAssetId = "destination"): LineageDataflow {
  return {
    id,
    dataflow_id: id,
    name: id,
    source_asset_id: sourceAssetId,
    destination_asset_id: destinationAssetId,
    metadata_source_id: 1,
    metadata_source_uri: "metadata.json"
  };
}

function reference(id: string, provenances: LineageReference["provenances"] = ["sql"]): LineageReference {
  return {
    id,
    entity_type: "reference",
    reference_type: "table_reference",
    display_name: "unknown.orders",
    normalized_value: "unknown.orders",
    group_status: "unresolved",
    resolved_asset_ids: [],
    candidate_asset_ids: [],
    occurrence_ids: [],
    consumer_asset_ids: [],
    provenances,
    dependency_count: 1,
    observations: [],
  };
}

function dependency(resolutionStatus: LineageDependency["resolution_status"], resolvedAssetId?: string): LineageDependency {
  return {
    id: `dependency-${resolutionStatus}`,
    target_asset_id: "target",
    consumer_asset_id: "target",
    kind: "reads",
    provenance: "sql",
    resolution_status: resolutionStatus,
    resolution_method: "test",
    reference_id: "reference-orders",
    reference_occurrence_id: "occurrence-orders",
    resolved_asset_id: resolvedAssetId,
    observations: [],
  };
}
