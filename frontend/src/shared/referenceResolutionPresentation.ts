import type { ReferenceResolution, ResolutionReason, ResolutionState } from "./api/domainTypes";

export interface ReferenceResolutionPresentation {
  state: ResolutionState;
  label: string;
  detail: string | null;
}

const labels: Record<ResolutionState, string> = {
  automatic: "Automatic",
  manual: "Manual",
  unresolved: "Unresolved",
};

const reasonLabels: Record<ResolutionReason, string> = {
  no_match: "No matching asset found",
  multiple_matches: "Multiple matching assets found",
  out_of_scope: "Outside source scope",
  conflicting_targets: "Occurrences resolve to different assets",
  incomplete: "Some occurrences are not resolved",
  target_missing: "The saved mapping target is unavailable",
};

export function presentReferenceResolution(
  value: ReferenceResolution | ResolutionState,
): ReferenceResolutionPresentation {
  const resolution = typeof value === "string" ? { state: value, reason: null } : value;
  return {
    state: resolution.state,
    label: labels[resolution.state],
    detail: resolution.reason ? reasonLabels[resolution.reason] : null,
  };
}
