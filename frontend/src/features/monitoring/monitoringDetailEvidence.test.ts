import { describe, expect, it } from "vitest";
import { monitoringDetailEvidenceRequest } from "./monitoringDetailEvidence";

const page = {
  limit: 100,
  offset: 200,
  sort: { sortBy: "duration_seconds", sortDir: "asc" as const },
};

describe("monitoring detail evidence request", () => {
  it("builds a bounded job child page", () => {
    const request = monitoringDetailEvidenceRequest(
      { kind: "job", row: { job_id: "job-1" } },
      { range: "30d" },
      page,
    );
    expect(request).toEqual({
      key: "job:job-1",
      params: {
        range: "30d",
        limit: 100,
        offset: 200,
        sortBy: "duration_seconds",
        sortDir: "asc",
        investigateKind: "job_id",
        investigateValue: "job-1",
      },
    });
  });

  it("keeps all operation types for a maintenance destination page", () => {
    const request = monitoringDetailEvidenceRequest(
      { kind: "maintenance", row: { target: "lakehouse::catalog.schema.table" } },
      { operationType: "maintenance", range: "7d" },
      page,
    );
    expect(request?.params).toMatchObject({
      operationType: "all",
      investigateKind: "destination_table",
      investigateValue: "lakehouse::catalog.schema.table",
      limit: 100,
      offset: 200,
    });
  });

  it("does not request evidence for non-paged drawers or missing identity", () => {
    expect(monitoringDetailEvidenceRequest({ kind: "diagnostics", row: {} }, {}, page)).toBeNull();
    expect(monitoringDetailEvidenceRequest({ kind: "freshness", row: {} }, {}, page)).toBeNull();
  });
});
