import { Search, X } from "lucide-react";
import { createPortal } from "react-dom";
import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import {
  metadataFilterOptionsForRender,
  type MetadataFilterOption
} from "./metadataColumnFilters";

interface MetadataColumnFilterPopoverProps {
  anchorRef: RefObject<HTMLElement>;
  columnKey: string;
  columnTitle: string;
  options: MetadataFilterOption[];
  selectedKeys: readonly string[];
  onApply: (selectedKeys: string[]) => void;
  onClear: () => void;
  onCancel: () => void;
}

interface PopoverPosition {
  left: number;
  top: number;
}

const POPOVER_WIDTH = 286;
const POPOVER_GAP = 5;

export function MetadataColumnFilterPopover({
  anchorRef,
  columnKey,
  columnTitle,
  options,
  selectedKeys,
  onApply,
  onClear,
  onCancel
}: MetadataColumnFilterPopoverProps) {
  const popoverRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [search, setSearch] = useState("");
  const [draftSelectedKeys, setDraftSelectedKeys] = useState(() => [...selectedKeys]);
  const [position, setPosition] = useState<PopoverPosition | null>(null);
  const selectedKeySet = new Set(draftSelectedKeys);
  const { matchingOptions, visibleOptions, truncated } = metadataFilterOptionsForRender(options, search);
  const allMatchingSelected = matchingOptions.length > 0 && matchingOptions.every((option) => selectedKeySet.has(option.key));
  const someMatchingSelected = matchingOptions.some((option) => selectedKeySet.has(option.key));
  const selectAllRef = useRef<HTMLInputElement>(null);

  useLayoutEffect(() => {
    searchRef.current?.focus();
  }, []);

  useLayoutEffect(() => {
    const updatePosition = () => {
      const anchor = anchorRef.current;
      const popover = popoverRef.current;
      if (!anchor) return;
      const anchorRect = anchor.getBoundingClientRect();
      const popoverHeight = popover?.getBoundingClientRect().height ?? 420;
      const maxLeft = Math.max(8, window.innerWidth - POPOVER_WIDTH - 8);
      const left = Math.min(Math.max(8, anchorRect.right - POPOVER_WIDTH), maxLeft);
      const belowTop = anchorRect.bottom + POPOVER_GAP;
      const top = belowTop + popoverHeight <= window.innerHeight - 8
        ? belowTop
        : Math.max(8, anchorRect.top - popoverHeight - POPOVER_GAP);
      setPosition((current) => current?.left === left && current.top === top ? current : { left, top });
    };

    updatePosition();
    window.addEventListener("resize", updatePosition);
    document.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      document.removeEventListener("scroll", updatePosition, true);
    };
  }, [anchorRef, options.length, search]);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      cancelAndRestoreFocus();
    };
    const closeWhenOutside = (event: PointerEvent) => {
      const target = event.target instanceof Node ? event.target : null;
      if (!target) return;
      if (anchorRef.current?.contains(target) || popoverRef.current?.contains(target)) return;
      if (target instanceof Element && target.closest("[data-metadata-column-filter-trigger]")) {
        onCancel();
        return;
      }
      cancelAndRestoreFocus();
    };
    document.addEventListener("keydown", closeOnEscape);
    document.addEventListener("pointerdown", closeWhenOutside, true);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      document.removeEventListener("pointerdown", closeWhenOutside, true);
    };
  }, [anchorRef, onCancel]);

  useEffect(() => {
    if (!selectAllRef.current) return;
    selectAllRef.current.indeterminate = !allMatchingSelected && someMatchingSelected;
  }, [allMatchingSelected, someMatchingSelected]);

  function toggleOption(key: string, checked: boolean) {
    setDraftSelectedKeys((current) => {
      const next = new Set(current);
      if (checked) next.add(key);
      else next.delete(key);
      return [...next];
    });
  }

  function toggleAll(checked: boolean) {
    setDraftSelectedKeys((current) => {
      const next = new Set(current);
      for (const option of matchingOptions) {
        if (checked) next.add(option.key);
        else next.delete(option.key);
      }
      return [...next];
    });
  }

  function restoreTriggerFocus() {
    const focusTrigger = () => {
      if (typeof document === "undefined") return;
      const anchor = anchorRef.current
        ?? [...document.querySelectorAll<HTMLElement>("[data-metadata-column-filter-trigger]")]
          .find((element) => element.dataset.metadataColumnFilterTrigger === columnKey);
      anchor?.focus();
    };
    if (typeof window === "undefined") {
      focusTrigger();
      return;
    }
    window.requestAnimationFrame(focusTrigger);
  }

  function cancelAndRestoreFocus() {
    onCancel();
    restoreTriggerFocus();
  }

  function clearAndRestoreFocus() {
    onClear();
    restoreTriggerFocus();
  }

  function applyAndRestoreFocus() {
    onApply(draftSelectedKeys);
    restoreTriggerFocus();
  }

  if (!position || typeof document === "undefined") return null;

  return createPortal(
    <div
      ref={popoverRef}
      className="metadata-column-filter-popover"
      role="dialog"
      aria-label={`Filter ${columnTitle}`}
      style={{ left: position.left, top: position.top, width: POPOVER_WIDTH }}
    >
      <div className="metadata-column-filter-heading">
        <strong>Filter by {columnTitle}</strong>
        <button type="button" onClick={cancelAndRestoreFocus} aria-label="Close filter menu" title="Cancel">
          <X size={15} />
        </button>
      </div>
      <label className="search-box metadata-column-filter-search">
        <Search size={15} />
        <input
          ref={searchRef}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search values"
          aria-label={`Search ${columnTitle} values`}
        />
      </label>
      <div className="metadata-column-filter-options" role="group" aria-label={`${columnTitle} values`}>
        <label className="metadata-column-filter-option metadata-column-filter-select-all">
          <input
            ref={selectAllRef}
            type="checkbox"
            checked={allMatchingSelected}
            disabled={!matchingOptions.length}
            onChange={(event) => toggleAll(event.target.checked)}
          />
          <span>{search.trim() ? "Select all matching values" : "(Select All)"}</span>
        </label>
        {visibleOptions.map((option) => (
          <label className="metadata-column-filter-option" key={option.key}>
            <input
              type="checkbox"
              checked={selectedKeySet.has(option.key)}
              onChange={(event) => toggleOption(option.key, event.target.checked)}
            />
            <span title={option.label}>{option.label}</span>
            <small>{option.count}</small>
          </label>
        ))}
        {!matchingOptions.length ? <div className="metadata-column-filter-empty">No matching values</div> : null}
        {truncated ? (
          <div className="metadata-column-filter-notice">
            Showing {visibleOptions.length} of {matchingOptions.length} values. Search to narrow the list.
          </div>
        ) : null}
      </div>
      <div className="metadata-column-filter-actions">
        <button type="button" className="text-action" onClick={clearAndRestoreFocus}>Clear</button>
        <span />
        <button type="button" className="text-action" onClick={cancelAndRestoreFocus}>Cancel</button>
        <button type="button" className="text-action primary" onClick={applyAndRestoreFocus}>Apply</button>
      </div>
    </div>,
    document.body
  );
}
