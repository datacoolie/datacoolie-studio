import { useState, type ReactNode } from "react";
import { isLosslessJsonNumber } from "../../../shared/api/monitoringAdapters";
import { Help } from "../../../shared/components/Help";
import { copyToClipboard, safeJsonStringify } from "./detailPrimitives";

export function DataflowCopyValue({ label, value, children, title }: { label: string; value: unknown; children?: ReactNode; title?: string }) {
  const [copied, setCopied] = useState(false);
  const exact = value === undefined ? "unk" : typeof value === "string" ? value : isLosslessJsonNumber(value) ? value.value : safeJsonStringify(value);
  return <>
    <Help as="button" type="button" className={`dataflow-value-copy-button${copied ? " is-copied" : ""}`} aria-label={`Copy ${label}`} title={copied ? "Copied" : `${title ? `${title}\n` : ""}Click to copy ${label}`} onClick={() => { void copyToClipboard(exact, setCopied); }}>{children ?? exact}</Help>
    <span className="dataflow-copy-feedback" role="status">{copied ? `${label} copied` : ""}</span>
  </>;
}
