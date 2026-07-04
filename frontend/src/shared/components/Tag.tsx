import type { ReactNode } from "react";

export type TagTone = "neutral" | "info" | "success" | "warning" | "danger";

interface TagProps {
  tone?: TagTone;
  children: ReactNode;
}

/** Compact status/label pill that consumes design tokens. */
export function Tag({ tone = "neutral", children }: TagProps) {
  return <span className={`ds-tag ds-tag-${tone}`}>{children}</span>;
}
