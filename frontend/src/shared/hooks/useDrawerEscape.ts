import { useEffect } from "react";
import { dismissActiveHelp } from "../components/helpCoordinator";

type DrawerEscapeEnabled = boolean | (() => boolean);

export function useDrawerEscape(onClose: () => void, enabled: DrawerEscapeEnabled = true) {
  useEffect(() => {
    if (enabled === false) return;

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (dismissActiveHelp(event)) return;
      if (typeof enabled === "function" && !enabled()) return;
      event.preventDefault();
      onClose();
    }

    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [enabled, onClose]);
}
