/** Source domain primitives shared across the workspace data layer and UI. */

export type SourceKind = "metadata" | "logs" | "code";

/** Stable map key for a source of a given kind. */
export function sourceKey(kind: SourceKind, id: number): string {
  return `${kind}:${id}`;
}
