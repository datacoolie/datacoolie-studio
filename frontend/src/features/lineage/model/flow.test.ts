import { describe, expect, it } from "vitest";
import type { LatestStatusResponse, LineageDataflow } from "../../../shared/api/types";
import { buildLineageFlow, latestRun } from "./flow";
import type { VisibleLineage } from "./types";

describe("lineage runtime evidence matching", () => {
  it("prefers exact dataflow ID over a same-name fallback", () => {
    const status: LatestStatusResponse = {
      latest: {},
      latest_by_id: { "flow-2": { status: "failed" } },
      latest_by_name: { duplicate: { status: "succeeded" } },
      ambiguous_names: ["duplicate"],
      errors: []
    };
    expect(latestRun(status, "flow-2", "duplicate")?.status).toBe("failed");
  });

  it("does not use the legacy name map when structured matching marks a name ambiguous", () => {
    const status: LatestStatusResponse = {
      latest: { duplicate: { status: "succeeded" } },
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

function dataflow(id: string): LineageDataflow {
  return {
    id,
    dataflow_id: id,
    name: id,
    source_asset_id: "source",
    destination_asset_id: "destination",
    metadata_source_id: 1,
    metadata_source_uri: "metadata.json"
  };
}
