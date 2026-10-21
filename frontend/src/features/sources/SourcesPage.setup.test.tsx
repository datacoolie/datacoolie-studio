import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { SourcePath } from "../../shared/api/domainTypes";
import { SourcesPage } from "./SourcesPage";

const projectManagedSqlSource = {
  id: 11,
  environment_id: 1,
  uri: "D:\\project\\sql\\finance",
  label: "finance",
  enabled: true,
  sync_schedule_enabled: false,
  created_at: "2026-09-16T00:00:00Z",
  source_config: {
    discovery_managed: true,
    component_resolved_from: { sql: "manifest.json" },
  },
  latest_validation: null,
} as SourcePath;

function renderSourcesSetup() {
  return renderToStaticMarkup(
    <SourcesPage
      metadataSources={[]}
      sqlSources={[projectManagedSqlSource]}
      logBasePaths={[]}
      codeArtifacts={[]}
      busy={false}
      selectedEnvironmentId={1}
      onImportMetadataSources={async () => null}
      onAddSqlSource={async () => undefined}
      onImportDatacoolieProjectSources={async () => null}
      onAddLogBasePath={async () => undefined}
      onAddCodeArtifact={async () => undefined}
      onUpdateSource={async () => undefined}
      onDeleteSource={async () => undefined}
      onGetDeleteImpact={async () => ({}) as never}
      onValidateSource={async () => ({}) as never}
      onSyncSource={async () => ({}) as never}
      onRetrySourceObservation={async () => ({}) as never}
      onRunSourceBatch={async () => ({}) as never}
      syncStatuses={{}}
      sourceOperations={{}}
      timezoneName="UTC"
    />,
  );
}

describe("SourcesPage source setup", () => {
  it("presents one required project root and repeatable SQL/code fallbacks", () => {
    const markup = renderSourcesSetup();

    expect(markup).toContain('id="project-root-input"');
    expect(markup).toContain('required=""');
    expect(markup).toContain("One root per scan");
    expect(markup).toContain("Marker files may define multiple SQL/code folders.");
    expect(markup).toContain('class="source-project-fallbacks"');
    expect(markup).toContain("Fallback folders");
    expect(markup).not.toContain("markers override these folders");
    expect(markup).toContain("Metadata");
    expect(markup).toContain("SQL");
    expect(markup).toContain("Source code");
    expect(markup).not.toContain("Metadata fallback folder</strong>");
    expect(markup).not.toContain("SQL fallback folders");
    expect(markup).not.toContain("Source-code fallback folders");
    expect(markup).toContain('id="project-sql-fallback-0"');
    expect(markup).toContain('id="project-code-fallback-0"');
    expect(markup).toContain("+Add");
    expect(markup).toContain('class="source-project-input-shell"');
    expect(markup).toContain('class="source-project-remove-path"');
    expect(markup).not.toContain("sql1,sql2");
    expect(markup).toContain("From manifest.json");
  });
});
