import { describe, expect, it } from "vitest";
import { MetadataSourceSaveConfirmationDialog } from "./MetadataSourceSaveConfirmationDialog";

const confirmation = {
  title: "Save 1 source file?",
  description: "This will update 1 source file.",
  detail: "The source file is backed up first.",
  impacts: [
    {
      action: "update" as const,
      key: "id:1",
      label: "metadata/orders.json",
      sheets: ["Dataflows"]
    }
  ]
};

describe("MetadataSourceSaveConfirmationDialog", () => {
  it("shows an animated saving state while the mutation is busy", () => {
    const dialog = MetadataSourceSaveConfirmationDialog({
      busy: true,
      confirmation,
      onCancel: () => undefined,
      onConfirm: () => undefined
    });

    expect(dialog.props.confirmLabel).toBe("Saving…");
    expect(dialog.props.confirmIcon.props.className).toBe("is-spinning");
  });

  it("shows the normal save action while idle", () => {
    const dialog = MetadataSourceSaveConfirmationDialog({
      busy: false,
      confirmation,
      onCancel: () => undefined,
      onConfirm: () => undefined
    });

    expect(dialog.props.confirmLabel).toBe("Save");
    expect(dialog.props.confirmIcon.props.className).toBeUndefined();
  });
});
