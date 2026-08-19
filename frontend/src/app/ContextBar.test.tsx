import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { EnvironmentContext } from "../shared/api/domainTypes";
import { ContextBar } from "./ContextBar";

const freshness = {
  status: "not_cached",
  message: "Logs are not synced",
  max_source_modified_at: "2026-08-18T12:00:00Z",
  metadata: {
    status: "current",
    max_source_modified_at: "2026-08-18T11:00:00Z",
    cache_synced_at: "2026-08-18T11:01:00Z",
    count: 2,
    pending_sync_count: 0,
  },
  etl_logs: {
    status: "not_cached",
    max_source_modified_at: "2026-08-18T12:00:00Z",
    cache_synced_at: "2026-08-18T11:58:00Z",
    count: 1,
    pending_sync_count: 1,
  },
} as EnvironmentContext["freshness"];

describe("ContextBar freshness", () => {
  it("keeps source modification and cache sync meaning distinct", () => {
    const markup = renderToStaticMarkup(
      <ContextBar
        activeModule="sources"
        scope="environment"
        project={{ id: 1, name: "Demo" }}
        environment={{ id: 2, name: "Dev" }}
        metadataSourceCount={2}
        logPathCount={1}
        freshness={freshness}
        timezoneName="UTC"
        onProjectSelect={vi.fn()}
        onOpenProject={vi.fn()}
      />,
    );

    expect(markup).toContain("Not synced");
    expect(markup).toContain("Source modified:");
    expect(markup).toContain("Cache: 1 of 1 paths not synced");
    expect(markup).toContain("Cache: aligned");
  });
});
