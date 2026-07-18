import { useEffect } from "react";

type DrawerEscapeEnabled = boolean | (() => boolean);

export function useDrawerEscape(onClose: () => void, enabled: DrawerEscapeEnabled = true) {
  useEffect(() => {
    if (enabled === false) return;

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (typeof enabled === "function" && !enabled()) return;
      event.preventDefault();
      onClose();
    }

    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [enabled, onClose]);
}
