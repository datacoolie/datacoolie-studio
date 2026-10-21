import { describe, expect, it } from "vitest";
import { DEFAULT_MONITORING_FILTERS, filtersFromSearch, filtersToSearch } from "./monitoringFilters";

describe("Monitoring route filter contract", () => {
  it("round trips exact empty, NULL and all session scope with numeric source identity", () => {
    for (const scope of ["exact", "null", "all"] as const) {
      const filters = { ...DEFAULT_MONITORING_FILTERS, investigateLogSessionScope: scope, investigateLogSessionId: "", investigateSourceId: 10 };
      const search = filtersToSearch(filters, "?extension=keep");
      expect(new URLSearchParams(search).get("extension")).toBe("keep");
      expect(new URLSearchParams(search).has("investigateLogSessionId")).toBe(scope === "exact");
      expect(filtersFromSearch(search)).toMatchObject({ investigateLogSessionScope: scope, investigateLogSessionId: "", investigateSourceId: 10 });
    }
  });
  it("rejects nonpositive source IDs and drops stale custom dates", () => {
    expect(filtersFromSearch("?investigateSourceId=NaN").investigateSourceId).toBeUndefined();
    expect(filtersFromSearch("?investigateSourceId=-1").investigateSourceId).toBeUndefined();
    expect(filtersToSearch({ ...DEFAULT_MONITORING_FILTERS, startTime: "old", endTime: "old" })).toBe("");
  });
});
