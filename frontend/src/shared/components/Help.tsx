import { createContext, useContext, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { ComponentPropsWithoutRef, CSSProperties, ElementType, ReactNode } from "react";
import { createPortal } from "react-dom";
import { CircleHelp } from "lucide-react";
import { claimHelp, dismissActiveHelp, releaseHelp } from "./helpCoordinator";
import type { HelpContent } from "./helpContent";
import { helpMetricValueColor } from "./helpContent";
import "./help.css";

const HelpContext = createContext(false);
const useBrowserLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;
export function useHelpEnabled() { return useContext(HelpContext); }

export function HelpProvider({ children, identity }: { children: ReactNode; identity?: unknown }) {
  useEffect(() => { dismissActiveHelp(); }, [identity]);
  useEffect(() => () => { dismissActiveHelp(); }, []);
  return <HelpContext.Provider value={true}>{children}</HelpContext.Provider>;
}

export function HelpContentView({ content }: { content: HelpContent }) {
  return <div className="help-content">
    {content.title !== undefined ? <div className="help-title">{content.title}</div> : null}
    {content.context !== undefined ? <div className="help-context">{content.context}</div> : null}
    {content.body !== undefined ? <div className="help-body">{content.body}</div> : null}
    {content.metrics?.length ? <dl className="help-metrics">{content.metrics.map(({label,value,tone = "neutral",valueColor,wrap}, index) => <div key={index} className={wrap ? `help-metric-${wrap}` : undefined}><dt>{label}</dt><dd className={`help-tone-${tone}`} style={{ color: helpMetricValueColor(valueColor) }}>{value}</dd></div>)}</dl> : null}
    {content.note !== undefined ? <div className="help-note">{content.note}</div> : null}
  </div>;
}

type HelpProps<T extends ElementType> = {
  as?: T;
  title?: string;
  content?: HelpContent;
  help?: ReactNode | (() => ReactNode);
  mode?: "tooltip" | "popover";
  identity?: unknown;
  children?: ReactNode;
} & Omit<ComponentPropsWithoutRef<T>, "title" | "content" | "as" | "children">;

/** Explicit opt-in replacement for informative title; no global DOM interception. */
export function Help<T extends ElementType = "span">({ as, title, content, help, mode = "tooltip", identity, children, ...props }: HelpProps<T>) {
  const Tag = as ?? "span";
  const id = useId();
  const trigger = useRef<HTMLElement | null>(null);
  const overlay = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 12, top: 12 });
  const suppressed = useRef(false);
  const pointerOnTrigger = useRef(false);
  const pointerOnOverlay = useRef(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout>>();
  const owner = useRef({ close: (restoreFocus?: boolean) => {
    suppressed.current = !!restoreFocus;
    setOpen(false);
    if (restoreFocus && mode === "popover") {
      trigger.current?.setAttribute("data-help-restoring-focus", "true");
      trigger.current?.focus();
      queueMicrotask(() => trigger.current?.removeAttribute("data-help-restoring-focus"));
    }
  } });
  const contentIdentity = JSON.stringify(content ?? title ?? "");
  const hasContent = !!(title || content || help);
  const cancelClose = () => { clearTimeout(closeTimer.current); };
  const close = () => { cancelClose(); releaseHelp(owner.current); setOpen(false); };
  const show = () => {
    cancelClose();
    if (!hasContent || suppressed.current) return;
    const drawers = [...document.querySelectorAll<HTMLElement>('.metadata-drawer,[aria-modal="true"]')].filter(element => element.getClientRects().length > 0);
    const visibleDrawer = drawers[drawers.length - 1];
    if (visibleDrawer && !visibleDrawer.contains(trigger.current)) return;
    if (trigger.current?.closest(".help-surface")) return;
    // A child value must not replace the owning compound explanation.
    if (mode === "tooltip" && trigger.current?.parentElement?.closest("[data-help-open='true']")) return;
    if (claimHelp(owner.current)) setOpen(true);
  };
  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = setTimeout(() => {
      if (!pointerOnTrigger.current && !pointerOnOverlay.current && !overlay.current?.contains(document.activeElement) && !trigger.current?.contains(document.activeElement)) close();
    }, 140);
  };
  useEffect(() => () => { clearTimeout(closeTimer.current); releaseHelp(owner.current); }, []);
  useEffect(() => { close(); suppressed.current = false; }, [contentIdentity, identity]);
  useBrowserLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const anchor = trigger.current;
      const surface = overlay.current;
      if (!anchor?.isConnected || !surface) { close(); return; }
      const rect = anchor.getBoundingClientRect();
      const box = surface.getBoundingClientRect();
      const width = document.documentElement.clientWidth;
      const height = window.innerHeight;
      const left = Math.max(12, Math.min(width - box.width - 12, rect.left));
      const below = rect.bottom + 8;
      const top = Math.max(12, Math.min(height - box.height - 12, below + box.height <= height - 12 ? below : rect.top - box.height - 8));
      setPosition({ left, top });
    };
    place();
    const observer = new ResizeObserver(place);
    if (overlay.current) observer.observe(overlay.current);
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") dismissActiveHelp(event); };
    const outside = (event: PointerEvent) => {
      if (!trigger.current?.contains(event.target as Node) && !overlay.current?.contains(event.target as Node)) close();
    };
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("pointerdown", outside, true);
    if (mode === "popover") overlay.current?.focus();
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("pointerdown", outside, true);
    };
  }, [open, mode]);
  const native = props as Record<string, any>;
  const triggerProps = {
    ...native,
    ref: trigger,
    "aria-describedby": mode === "tooltip" && open ? [native["aria-describedby"], id].filter(Boolean).join(" ") : native["aria-describedby"],
    "aria-expanded": mode === "popover" ? open : native["aria-expanded"],
    "aria-controls": mode === "popover" ? id : native["aria-controls"],
    "aria-description": title ?? (content ? [content.title, content.context, content.body, ...(content.metrics ?? []).map(metric => `${metric.label}: ${metric.value}`),content.note].filter(Boolean).join("\n") : native["aria-description"]),
    "data-help-open": open,
    onPointerEnter: (event: any) => { native.onPointerEnter?.(event); pointerOnTrigger.current = true; suppressed.current = false; if (mode === "tooltip") show(); },
    onPointerMove: (event: any) => { native.onPointerMove?.(event); if (mode === "tooltip" && pointerOnTrigger.current && !open) { suppressed.current = false; show(); } },
    onPointerLeave: (event: any) => { native.onPointerLeave?.(event); pointerOnTrigger.current = false; if (mode === "tooltip") scheduleClose(); },
    onFocus: (event: any) => {
      native.onFocus?.(event);
      if (!trigger.current?.contains(event.target)) return;
      if (!trigger.current?.contains(event.relatedTarget) && !(event.relatedTarget as HTMLElement | null)?.closest?.(".help-surface")) suppressed.current = false;
      if (mode === "tooltip" && !event.target.closest(".help-info-button, [data-help-restoring-focus]")) show();
    },
    onBlur: (event: any) => { native.onBlur?.(event); if (mode === "tooltip") scheduleClose(); },
    onClick: (event: any) => {
      native.onClick?.(event);
      if (mode === "popover") { event.stopPropagation(); suppressed.current = false; if (open) close(); else show(); }
      else if (event.detail === 0) show();
    },
  };
  // Keep modal help within its owning focus trap, including React portals.
  const portalRoot = trigger.current?.closest('[role="dialog"], .metadata-drawer') ?? (typeof document !== "undefined" ? document.body : null);
  return <><Tag {...triggerProps}>{children}</Tag>{open && portalRoot ? createPortal(
    <div ref={overlay} id={id} role={mode === "popover" ? "dialog" : "tooltip"} aria-label={mode === "popover" ? content?.title ?? title ?? "Information" : undefined} tabIndex={mode === "popover" ? -1 : undefined} className={`help-surface help-${mode}`} style={position} onClick={(event) => event.stopPropagation()} onMouseDown={(event) => event.stopPropagation()} onPointerDown={(event) => event.stopPropagation()} onPointerEnter={() => { pointerOnOverlay.current = true; cancelClose(); }} onPointerLeave={() => { pointerOnOverlay.current = false; if (mode === "tooltip") scheduleClose(); }}>
      {help ? typeof help === "function" ? help() : help : <HelpContentView content={content ?? {body: title}} />}
    </div>, portalRoot) : null}</>;
}

export function HelpButton({ title, content, help, label = "More information", className, style, identity }: { title?: string; content?: HelpContent; help?: ReactNode | (() => ReactNode); label?: string; className?: string; style?: CSSProperties; identity?: unknown }) {
  return <Help as="button" type="button" className={["help-info-button", className].filter(Boolean).join(" ")} style={style} mode="popover" title={title} content={content} help={help} identity={identity} aria-label={label}><CircleHelp size={14} aria-hidden="true" /></Help>;
}
