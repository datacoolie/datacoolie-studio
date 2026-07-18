export type ReferenceResolutionState =
  | "automatic"
  | "manual"
  | "needs_mapping"
  | "review"
  | "partial"
  | "missing_target"
  | "inactive"
  | "stored_only";

export interface ReferenceResolutionPresentation {
  state: ReferenceResolutionState;
  label: string;
}

const labels: Record<ReferenceResolutionState, string> = {
  automatic: "Automatic",
  manual: "Manual",
  needs_mapping: "Needs mapping",
  review: "Review",
  partial: "Review",
  missing_target: "Target missing",
  inactive: "Saved, not applied",
  stored_only: "Saved only",
};

export function presentReferenceResolution(state: ReferenceResolutionState): ReferenceResolutionPresentation {
  return { state, label: labels[state] };
}
