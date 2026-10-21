type HelpOwner = { close: (restoreFocus?: boolean) => void };
let active: HelpOwner | null = null;
let escapeBlocked = false;
let observingIntent = false;
let pointerPosition: string | null = null;

function observeIntent() {
  if (observingIntent || typeof document === "undefined") return;
  observingIntent = true;
  document.addEventListener("pointermove", event => {
    const position = `${event.clientX}:${event.clientY}`;
    if (pointerPosition !== position) escapeBlocked = false;
    pointerPosition = position;
  }, true);
  document.addEventListener("pointerdown", () => { escapeBlocked = false; }, true);
  document.addEventListener("keydown", event => { if (event.key !== "Escape") escapeBlocked = false; }, true);
}

/** React and chart help share arbitration, including capture-phase modal Escape. */
export function claimHelp(owner: HelpOwner) {
  observeIntent();
  // Removing a surface can emit pointerenter for the newly uncovered element.
  // Escape stays dismissed until a real pointer, key or tap action follows.
  if (escapeBlocked) return false;
  if (active !== owner) active?.close();
  active = owner;
  return true;
}

export function releaseHelp(owner: HelpOwner) {
  if (active === owner) active = null;
}

export function dismissActiveHelp(event?: KeyboardEvent): boolean {
  if (event?.defaultPrevented) return false;
  if (!active) return false;
  const owner = active;
  active = null;
  escapeBlocked = !!event;
  owner.close(true);
  event?.preventDefault();
  event?.stopPropagation();
  event?.stopImmediatePropagation();
  return true;
}
