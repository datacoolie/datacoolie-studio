import { renderToStaticMarkup } from "react-dom/server";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import type { SourceDeleteImpact } from "../../shared/api/contractTypes";
import type { SourcePath } from "../../shared/api/domainTypes";
import { LogSyncDialog, SourceDeleteDialog } from "./SourceDialogs";

vi.mock("../../shared/components/OperationConfirmationDialog", () => ({
  OperationConfirmationDialog: ({ title, confirmLabel, children }: { title: string; confirmLabel: string; children?: ReactNode }) => (
    <div>
      <h1>{title}</h1>
      <button type="button">{confirmLabel}</button>
      {children}
    </div>
  ),
}));

describe("source dialogs", () => {
  it("renders the log sync mode confirmation", () => {
    const markup = renderToStaticMarkup(
      <LogSyncDialog sourceCount={2} onCancel={() => undefined} onConfirm={() => undefined} />,
    );

    expect(markup).toContain("Sync 2 sources");
    expect(markup).toContain("Incremental");
    expect(markup).toContain("Incremental + lookback");
  });

  it("renders delete impact and source safety copy", () => {
    const markup = renderToStaticMarkup(
      <SourceDeleteDialog
        prompt={{
          entries: [{ kind: "metadata", source: { id: 7 } as SourcePath }],
          impacts: [{
            source_id: 7,
            source_kind: "metadata",
            impacts: [{ kind: "metadata", label: "Metadata rows", count: 3, severity: "info" }],
          } as SourceDeleteImpact],
        }}
        onCancel={() => undefined}
        onConfirm={() => undefined}
      />,
    );

    expect(markup).toContain("Delete metadata source?");
    expect(markup).toContain("Metadata rows");
    expect(markup).toContain("Original source files will not be deleted.");
  });
});
