import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { MetadataSheetToolbar } from "./MetadataSheetToolbar";

function renderToolbar(overrides: Partial<Parameters<typeof MetadataSheetToolbar>[0]> = {}) {
  const noop = vi.fn();
  return renderToStaticMarkup(
    <MetadataSheetToolbar
      activeSheet="dataflows"
      busy={false}
      savingDraft={false}
      hasLocalChanges
      hasSourceChanges
      hasStoredDraft={false}
      filteredRowCount={1}
      totalRowCount={1}
      mode="edit"
      query=""
      sourceFormat="merged"
      sourceUri="environment://metadata"
      onActiveSheetChange={noop}
      onDiscard={noop}
      onDiscardDraft={noop}
      onHistoryOpen={noop}
      onModeChange={noop}
      onQueryChange={noop}
      onSave={noop}
      onSaveDraft={noop}
      {...overrides}
    />,
  );
}

describe("MetadataSheetToolbar", () => {
  it("shows an accessible animated draft-saving state", () => {
    const markup = renderToolbar({ busy: true, savingDraft: true });

    expect(markup).toContain("Saving draft…");
    expect(markup).toContain('aria-busy="true"');
    expect(markup).toContain("is-spinning");
  });

  it("shows a concise removable exact-stage filter", () => {
    const markup = renderToolbar({ stageFilter: "silver2" });

    expect(markup).toContain("Stage: silver2");
    expect(markup).toContain("Clear stage filter");
    expect(markup).toContain("metadata-stage-tone");
  });

  it("shows a removable summary for active column filters", () => {
    const markup = renderToolbar({ activeColumnFilterCount: 2 });

    expect(markup).toContain("2 column filters");
    expect(markup).toContain("Clear column filters");
    expect(markup).toContain("1 of 1 rows");
  });

  it("keeps draft saving available when source writes are disabled", () => {
    const markup = renderToolbar({ readOnly: true });

    expect(markup).toContain("Save draft");
    expect(markup).not.toContain("Save to source");
  });
});
