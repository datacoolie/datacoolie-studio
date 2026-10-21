import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type {
  SourcePath,
} from "../../shared/api/domainTypes";
import type { SourceSyncStatus } from "../../shared/api/contractTypes";
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
  validation: {
    state: "not_validated",
    completed_at: null,
    summary: null,
    error: null,
  },
  observation: {
    state: "paused",
    checked_at: "2026-07-30T12:00:00Z",
    next_check_at: null,
    pending_changes: null,
    failure_count: 3,
    error: { code: "storage_access_failed", message: "access denied" },
  },
  sync_execution: {
    state: "never",
    trigger: null,
    job_id: null,
    started_at: null,
    completed_at: null,
    last_successful_at: null,
    summary: null,
    error: null,
    result: null,
  },
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
      sqlSources={[]}
      logBasePaths={[]}
      codeArtifacts={[]}
      busy={false}
      selectedEnvironmentId={3}
      onImportMetadataSources={async () => null}
      onAddSqlSource={async () => undefined}
      onImportDatacoolieProjectSources={async () => null}
      onAddLogBasePath={async () => undefined}
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
    expect(markup).not.toContain("Automatic layout + artifacts");
    expect(markup).not.toContain("Only this folder");
  });

  it("shows retry progress and disables the retry action", () => {
    const markup = renderPausedSource(true);

    expect(markup).toContain("Retrying...");
    expect(markup).toContain('aria-busy="true"');
    expect(markup).toContain("disabled");
  });

  it("shows repeatable SQL/code fallback rows without implying comma-separated paths", () => {
    const markup = renderPausedSource();

    expect(markup).toContain("Project root");
    expect(markup).toContain("Fallback folders");
    expect(markup).toContain("SQL");
    expect(markup).toContain("Source code");
    expect(markup).not.toContain("Metadata fallback folder</strong>");
    expect(markup).not.toContain("SQL fallback folders");
    expect(markup).not.toContain("Source-code fallback folders");
    expect(markup).toContain("Marker files may define multiple SQL/code folders.");
    expect(markup).toContain('placeholder="metadata"');
    expect(markup).toContain('placeholder="sql"');
    expect(markup).toContain('placeholder="functions"');
    expect(markup).toContain('aria-label="Metadata fallback folder (default: metadata)"');
    expect(markup).toContain('aria-label="SQL fallback folder 1"');
    expect(markup).toContain('aria-label="Source-code fallback folder 1"');
    expect(markup).toContain("+Add");
    expect(markup).not.toContain("sql1,sql2");
    expect(markup).not.toContain("From marker");
    expect(markup).not.toContain("Include metadata");
    expect(markup).not.toContain("Include source code");
  });
});
