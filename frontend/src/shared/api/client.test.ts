import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./client";

const monitoringResponse = {
  records: [],
  errors: [],
  summary: { records: 0, total_records: 0, limit: 0, offset: 0, cache: null },
};

describe("API query serialization", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("preserves an exact empty session id while keeping null/all scopes distinct", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => monitoringResponse,
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.getMonitoringJobs(7, {
      log_session_scope: "exact",
      log_session_id: "",
      q: "",
    });
    await api.getMonitoringJobs(7, { log_session_scope: "null" });
    await api.getMonitoringJobs(7, { log_session_scope: "all" });

    const searches = fetchMock.mock.calls.map(([input]) =>
      new URL(String(input), "http://studio.test").searchParams,
    );
    expect(searches[0].get("log_session_scope")).toBe("exact");
    expect(searches[0].has("log_session_id")).toBe(true);
    expect(searches[0].get("log_session_id")).toBe("");
    expect(searches[0].has("q")).toBe(false);
    expect(searches[1].get("log_session_scope")).toBe("null");
    expect(searches[1].has("log_session_id")).toBe(false);
    expect(searches[2].get("log_session_scope")).toBe("all");
    expect(searches[2].has("log_session_id")).toBe(false);
  });
});
