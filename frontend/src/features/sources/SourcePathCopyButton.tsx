import { Check, Copy } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export function SourcePathCopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const resetTimer = useRef<number | null>(null);

  useEffect(() => () => {
    if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
  }, []);

  async function copyPath() {
    if (await writeSourcePathToClipboard(value)) {
      setCopied(true);
      if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
      resetTimer.current = window.setTimeout(() => setCopied(false), 1400);
    } else {
      setCopied(false);
    }
  }

  const accessibleLabel = copied ? `${label} copied` : `Copy ${label.toLowerCase()}`;
  return (
    <button
      type="button"
      className={`source-card-path-copy${copied ? " is-copied" : ""}`}
      aria-label={accessibleLabel}
      title={copied ? "Copied" : `Copy ${label.toLowerCase()}`}
      onClick={() => void copyPath()}
    >
      {copied ? <Check size={13} /> : <Copy size={13} />}
    </button>
  );
}

async function writeSourcePathToClipboard(value: string) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      // Fall through for browsers that expose the API without granting access.
    }
  }

  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  return copied;
}
