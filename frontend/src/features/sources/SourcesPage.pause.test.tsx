import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type {
  SourcePath,
  SourceSyncStatus,
} from "../../shared/api/domainTypes";
import { sourceKey } from "../../shared/lib/sources";
import { SourcesPage } from "./SourcesPage";
import { beginSourceOperations } from "./sourceWorkspaceModel";

const metadataSource = {
  id: 7,
  environment_id: 3,
  uri: "D:\\workspace\\metadata.json",
  label: "Metadata",
  enabled: true,
  source_config: {},
  latest_validation: null,
} as SourcePath;

const pausedStatus = {
  source_id: 7,
  source_kind: "metadata",
  status: "error",
  message: "access denied",
  error: { code: "storage_access_failed", message: "access denied" },
  checked_at: "2026-07-30T12:00:00Z",
  next_check_at: null,
  observation_state: "paused",
  observation_failure_count: 3,
  observation_paused_at: "2026-07-30T12:00:00Z",
  latest_job: null,
} as SourceSyncStatus;

function renderPausedSource(retrying = false) {
  const operations = retrying
    ? beginSourceOperations(
        {},
        3,
        [{ kind: "metadata", id: 7 }],
        "retry",
      )
    : {};
  return renderToStaticMarkup(
    <SourcesPage
      metadataSources={[metadataSource]}
      logPaths={[]}
      codeArtifacts={[]}
      busy={false}
      selectedEnvironmentId={3}
      onImportMetadataSources={async () => null}
      onImportDatacoolieProjectSources={async () => null}
      onAddLogPath={async () => undefined}
      onAddCodeArtifact={async () => undefined}
      onUpdateSource={async () => undefined}
      onDeleteSource={async () => undefined}
      onGetDeleteImpact={vi.fn()}
      onValidateSource={vi.fn()}
      onSyncSource={vi.fn()}
      onRetrySourceObservation={vi.fn()}
      onRunSourceBatch={vi.fn()}
      syncStatuses={{
        [sourceKey("metadata", metadataSource.id)]: pausedStatus,
      }}
      sourceOperations={operations}
      timezoneName="UTC"
    />,
  );
}

describe("SourcesPage paused observation", () => {
  it("shows the pause reason and agreed recovery actions", () => {
    const markup = renderPausedSource();

    expect(markup).toContain("Source checks paused");
    expect(markup).toContain("3 consecutive automatic checks failed");
    expect(markup).toContain("Last error: access denied");
    expect(markup).toContain("Retry now");
    expect(markup).toContain("Successful Validate or Sync");
    expect(markup).toContain('aria-label="Copy path"');
  });

  it("shows retry progress and disables the retry action", () => {
    const markup = renderPausedSource(true);

    expect(markup).toContain("Retrying...");
    expect(markup).toContain('aria-busy="true"');
    expect(markup).toContain("disabled");
  });
});
