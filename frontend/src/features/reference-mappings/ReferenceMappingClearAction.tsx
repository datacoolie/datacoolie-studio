import { useEffect, useRef } from "react";

interface ReferenceMappingClearActionProps {
  confirming: boolean;
  disabled: boolean;
  onClear: () => void;
  onDismiss: () => void;
}

export function ReferenceMappingClearAction({ confirming, disabled, onClear, onDismiss }: ReferenceMappingClearActionProps) {
  const actionRef = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    if (!confirming) return undefined;
    function dismissOnOutsidePointer(event: PointerEvent) {
      if (event.target instanceof Node && !actionRef.current?.contains(event.target)) onDismiss();
    }
    function dismissOnEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onDismiss();
    }
    document.addEventListener("pointerdown", dismissOnOutsidePointer, true);
    document.addEventListener("keydown", dismissOnEscape);
    return () => {
      document.removeEventListener("pointerdown", dismissOnOutsidePointer, true);
      document.removeEventListener("keydown", dismissOnEscape);
    };
  }, [confirming, onDismiss]);

  return (
    <span ref={actionRef}>
      <button
        className={confirming ? "text-action reference-mapping-action-clear confirm" : "text-action reference-mapping-action-clear"}
        type="button"
        disabled={disabled}
        onClick={onClear}
      >
        {confirming ? "Confirm clear" : "Clear"}
      </button>
    </span>
  );
}
