import type { ButtonHTMLAttributes } from "react";

interface ToggleProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onChange" | "onToggle"> {
  checked: boolean;
  onToggle: (next: boolean) => void;
  label: string;
}

/**
 * Accessible on/off switch. Renders a button with `role="switch"` so it works
 * with keyboards and assistive technology.
 */
export function Toggle({ checked, onToggle, label, disabled, className, ...rest }: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={["ds-toggle", checked ? "is-on" : "", className ?? ""].filter(Boolean).join(" ")}
      onClick={() => onToggle(!checked)}
      {...rest}
    >
      <span className="ds-toggle-thumb" aria-hidden="true" />
    </button>
  );
}
