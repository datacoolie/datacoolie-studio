import { X } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

type ConfirmationTone = "primary" | "warning" | "danger";

interface OperationConfirmationDialogProps {
  children?: ReactNode;
  confirmIcon?: ReactNode;
  confirmLabel: string;
  description: string;
  icon: ReactNode;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  title: string;
  tone?: ConfirmationTone;
}

export function OperationConfirmationDialog({
  children,
  confirmIcon,
  confirmLabel,
  description,
  icon,
  busy = false,
  onCancel,
  onConfirm,
  title,
  tone = "primary"
}: OperationConfirmationDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const cancelButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    cancelButtonRef.current?.focus();
    return () => {
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
    };
  }, []);

  useEffect(() => {
    if (busy) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, onCancel]);

  const dismiss = () => {
    if (!busy) onCancel();
  };

  return createPortal(
    <div
      className="operation-confirmation-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) dismiss();
      }}
    >
      <section
        className={`operation-confirmation-dialog tone-${tone}`}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        onKeyDown={(event) => {
          if (event.key !== "Tab") return;
          const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"));
          if (!focusable.length) return;
          const first = focusable[0];
          const last = focusable.at(-1);
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last?.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
          }
        }}
      >
        <header>
          <span className="operation-confirmation-icon">{icon}</span>
          <div>
            <h2 id={titleId}>{title}</h2>
            <p id={descriptionId}>{description}</p>
          </div>
          <button type="button" className="operation-confirmation-close" onClick={dismiss} disabled={busy} aria-label="Close">
            <X size={16} />
          </button>
        </header>
        {children ? <div className="operation-confirmation-body">{children}</div> : null}
        <footer>
          <button ref={cancelButtonRef} type="button" className="operation-confirmation-cancel" onClick={dismiss} disabled={busy}>Cancel</button>
          <button type="button" className={`operation-confirmation-confirm tone-${tone}`} onClick={onConfirm} disabled={busy}>
            {confirmIcon}
            {confirmLabel}
          </button>
        </footer>
      </section>
    </div>,
    document.body
  );
}
