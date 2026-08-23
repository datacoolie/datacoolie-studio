import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../../shared/api/client";
import type { SourceReadCheckResult } from "../../shared/api/domainTypes";
import { createEnvironmentSourceMutations } from "./sourceMutations";

function validationError(): SourceReadCheckResult {
  return {
    source_id: 9,
    source_kind: "metadata",
    status: "error",
    message: "Azure credential belongs to a different tenant",
    errors: [{ message: "Azure credential belongs to a different tenant" }],
  };
}

function createContext() {
  return {
    environmentId: 6,
    module: "sources" as const,
    activeEnvironmentIdRef: { current: 6 },
    setBusy: vi.fn(),
    setError: vi.fn(),
    setSourceSyncStatuses: vi.fn(),
    setSourceOperations: vi.fn(),
    invalidateProjectSummaries: vi.fn(),
    refreshEnvironment: vi.fn(async () => undefined),
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("source validation mutations", () => {
  it("surfaces a terminal validation error and still clears the operation", async () => {
    vi.spyOn(api, "validateMetadataSource").mockResolvedValue(validationError());
    const context = createContext();
    const mutations = createEnvironmentSourceMutations(context);

    const result = await mutations.validateSource("metadata", 9);

    expect(result.status).toBe("error");
    expect(context.setError).toHaveBeenCalledWith(
      "Azure credential belongs to a different tenant",
    );
    expect(context.setSourceOperations).toHaveBeenCalledTimes(2);
    expect(context.refreshEnvironment).toHaveBeenCalledWith(
      6,
      "sources",
      { forceHeader: true, forceModule: true },
    );
  });
});
