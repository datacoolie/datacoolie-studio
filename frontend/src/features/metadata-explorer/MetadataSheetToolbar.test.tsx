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
      onValidate={noop}
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
});
