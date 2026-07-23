import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../shared/api/client";
import type { LatestStatusResponse, LineageResponse, MonitoringRecord, MonitoringRecordsResponse } from "../../shared/api/domainTypes";
import { environmentQueryKeys } from "../environments/environmentQueries";
import {
  lineageDataflowRunsOptions,
  lineageGraphOptions,
  lineageLatestStatusOptions,
  lineageQueryKeys,
} from "./lineageQueries";

const graph = {
  schema_version: "lineage.v4",
  summary: { assets: 0, references: 0, dataflows: 0, dependencies: 0, stitched_assets: 0, declared_assets: 0, automatic_references: 0, manual_references: 0, unresolved_references: 0, automatic_dependencies: 0, manual_dependencies: 0, unresolved_dependencies: 0, diagnostics: 0 },
  assets: [], references: [], dataflows: [], dependencies: [],
} as LineageResponse;
const latest = { latest_by_id: {}, latest_by_name: {}, ambiguous_names: [], errors: [] } as LatestStatusResponse;

afterEach(() => vi.restoreAllMocks());

describe("Lineage query ownership", () => {
  it("separates structural and operations-backed identities by Environment", () => {
    expect(lineageQueryKeys.graph(7)).not.toEqual(lineageQueryKeys.graph(8));
    expect(lineageQueryKeys.graph(7)).not.toEqual(lineageQueryKeys.latestStatus(7));
    expect(lineageQueryKeys.dataflowRuns(7, "a", "A")).not.toEqual(lineageQueryKeys.dataflowRuns(7, "b", "B"));
  });

  it("reuses fresh graph and latest-status reads", async () => {
    const client = new QueryClient();
    const graphRequest = vi.spyOn(api, "getLineage").mockResolvedValue(graph);
    const latestRequest = vi.spyOn(api, "getLatestStatus").mockResolvedValue(latest);
    await client.fetchQuery(lineageGraphOptions(7));
    await client.fetchQuery(lineageGraphOptions(7));
    await client.fetchQuery(lineageLatestStatusOptions(7));
    await client.fetchQuery(lineageLatestStatusOptions(7));
    expect(graphRequest).toHaveBeenCalledTimes(1);
    expect(latestRequest).toHaveBeenCalledTimes(1);
  });

  it("caches exact dataflow run history and refetches after operations invalidation", async () => {
    const client = new QueryClient();
    const response = { records: [
      { dataflow_id: "orders", dataflow_name: "Orders" },
      { dataflow_id: "other", dataflow_name: "Other" },
    ], errors: [], summary: { records: 2, limit: 25 } } as MonitoringRecordsResponse<MonitoringRecord>;
    const request = vi.spyOn(api, "getMonitoringDataflows").mockResolvedValue(response);
    const options = lineageDataflowRunsOptions(7, "orders", "Orders");
    expect(await client.fetchQuery(options)).toHaveLength(1);
    expect(await client.fetchQuery(options)).toHaveLength(1);
    await client.invalidateQueries({ queryKey: environmentQueryKeys.monitoring(7) });
    expect(await client.fetchQuery(options)).toHaveLength(1);
    expect(request).toHaveBeenCalledTimes(2);
  });
});
