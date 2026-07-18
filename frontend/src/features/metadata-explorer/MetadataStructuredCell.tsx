import { Check, Clipboard, Pin, X } from "lucide-react";
import type React from "react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { CellComponent } from "react-datasheet-grid";
import {
  formatCellValue,
  formatPrettyStructuredValue,
  structuredCellKind,
  type StructuredCellKind
} from "./metadataSheetOperations";

interface MetadataStructuredColumnData {
  columnKey: string;
  readOnly?: boolean;
}

interface TooltipPosition {
  left: number;
  top: number;
  width: number;
  pinned: boolean;
}

interface EditorPosition {
  left: number;
  top: number;
  width: number;
  height: number;
  maxHeight: number;
}

interface EditorSize {
  width: number;
  height: number;
}

const MIN_EDITOR_WIDTH = 420;
const MIN_EDITOR_HEIGHT = 350;
const DEFAULT_EDITOR_WIDTH = 720;
const DEFAULT_EDITOR_HEIGHT = 350;
const MAX_EDITOR_WIDTH = 720;
const MAX_EDITOR_HEIGHT = 520;
let preferredEditorSize: EditorSize | null = null;
let jsonContentRuler: HTMLPreElement | null | undefined;
let jsonTooltipRuler: HTMLPreElement | null | undefined;

export const MetadataStructuredCell: CellComponent<unknown, MetadataStructuredColumnData> = ({
  rowData,
  setRowData,
  active,
  focus,
  disabled,
  columnData,
  stopEditing
}) => {
  const cellRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLPreElement>(null);
  const editorRef = useRef<HTMLElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const suppressNextOpenRef = useRef(false);
  const pendingTypedValueRef = useRef<string | null>(null);
  const activeBeforePointerDownRef = useRef(false);
  const resizingRef = useRef(false);
  const kind = structuredCellKind(columnData.columnKey, rowData);
  const readOnly = Boolean(columnData.readOnly);
  const prettyValue = kind === "sql" ? formatPrettySql(formatCellValue(rowData)) : formatPrettyStructuredValue(rowData);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorValue, setEditorValue] = useState("");
  const [editorPosition, setEditorPosition] = useState<EditorPosition | null>(null);
  const [error, setError] = useState("");
  const [tooltipPosition, setTooltipPosition] = useState<TooltipPosition | null>(null);
  const highlightedPrettyValue = highlightStructuredValue(prettyValue, kind);

  useEffect(() => {
    if (!focus || !kind) {
      setEditorOpen(false);
      if (!focus) suppressNextOpenRef.current = false;
      return;
    }
    if (disabled || readOnly) {
      showPreview(true);
      stopEditing({ nextRow: false });
      return;
    }
    if (suppressNextOpenRef.current) {
      suppressNextOpenRef.current = false;
      return;
    }
    openEditor(pendingTypedValueRef.current ?? undefined);
    pendingTypedValueRef.current = null;
  }, [disabled, focus, kind, prettyValue, readOnly]);

  useEffect(() => {
    if (!active || focus || disabled || readOnly || !kind) return;
    const capturePrintableKey = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
      if (event.key.length !== 1) return;
      pendingTypedValueRef.current = event.key;
    };
    document.addEventListener("keydown", capturePrintableKey, true);
    return () => document.removeEventListener("keydown", capturePrintableKey, true);
  }, [active, disabled, focus, kind, readOnly]);

  useLayoutEffect(() => {
    if (!editorOpen) return;
    textareaRef.current?.focus();
    textareaRef.current?.setSelectionRange(0, 0);
  }, [editorOpen]);

  useLayoutEffect(() => {
    if (!tooltipPosition || !tooltipRef.current || !cellRef.current) return;
    const cellRect = cellRef.current.getBoundingClientRect();
    const tooltipRect = tooltipRef.current.getBoundingClientRect();
    const left = calculateAnchoredLeft(cellRect, tooltipRect.width);
    const top = calculateAnchoredTop(cellRect, tooltipRect.height);
    if (
      Math.abs(left - tooltipPosition.left) < 0.5
      && Math.abs(top - tooltipPosition.top) < 0.5
      && Math.abs(tooltipRect.width - tooltipPosition.width) < 0.5
    ) return;
    setTooltipPosition((current) => current ? {
      ...current,
      left,
      top,
      width: tooltipRect.width
    } : current);
  }, [tooltipPosition]);

  useEffect(() => {
    if (!tooltipPosition?.pinned) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setTooltipPosition(null);
    };
    const closeWhenOutside = (event: PointerEvent | FocusEvent) => {
      const target = event.target instanceof Node ? event.target : null;
      if (!target) return;
      if (cellRef.current?.contains(target) || tooltipRef.current?.contains(target)) return;
      setTooltipPosition(null);
    };
    document.addEventListener("keydown", closeOnEscape);
    document.addEventListener("pointerdown", closeWhenOutside, true);
    document.addEventListener("focusin", closeWhenOutside, true);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      document.removeEventListener("pointerdown", closeWhenOutside, true);
      document.removeEventListener("focusin", closeWhenOutside, true);
    };
  }, [tooltipPosition?.pinned]);

  useEffect(() => {
    if (!tooltipPosition?.pinned) return;
    const reposition = (event?: Event) => {
      const target = event?.target instanceof Node ? event.target : null;
      if (target && tooltipRef.current?.contains(target)) return;
      const nextPosition = calculateTooltipPosition(true);
      if (nextPosition) setTooltipPosition(nextPosition);
    };
    document.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
    return () => {
      document.removeEventListener("scroll", reposition, true);
      window.removeEventListener("resize", reposition);
    };
  }, [tooltipPosition?.pinned, prettyValue]);

  useEffect(() => {
    if (!editorOpen) return;
    const closeWhenOutside = (event: PointerEvent | FocusEvent) => {
      const target = event.target instanceof Node ? event.target : null;
      if (!target) return;
      if (cellRef.current?.contains(target) || editorRef.current?.contains(target)) return;
      closeEditor();
    };
    document.addEventListener("pointerdown", closeWhenOutside, true);
    document.addEventListener("focusin", closeWhenOutside, true);
    return () => {
      document.removeEventListener("pointerdown", closeWhenOutside, true);
      document.removeEventListener("focusin", closeWhenOutside, true);
    };
  }, [editorOpen]);

  useEffect(() => {
    if (!editorOpen) return;
    const reposition = (event?: Event) => {
      const target = event?.target instanceof Node ? event.target : null;
      if (target && editorRef.current?.contains(target)) return;
      const nextPosition = calculateEditorPosition(editorValue);
      if (nextPosition) setEditorPosition(nextPosition);
    };
    document.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
    return () => {
      document.removeEventListener("scroll", reposition, true);
      window.removeEventListener("resize", reposition);
    };
  }, [editorOpen, editorValue]);

  function closeEditor() {
    suppressNextOpenRef.current = true;
    setEditorOpen(false);
    setEditorPosition(null);
    setError("");
    stopEditing({ nextRow: false });
  }

  function openEditor(initialOverride?: string) {
    if (!kind || disabled || readOnly) return;
    const initialValue = initialOverride ?? (prettyValue || defaultStructuredValue(kind));
    const position = calculateEditorPosition(initialValue);
    if (!position) return;
    setEditorValue(initialValue);
    setError("");
    setTooltipPosition(null);
    setEditorPosition(position);
    setEditorOpen(true);
  }

  function calculateEditorPosition(value: string) {
    const rect = cellRef.current?.getBoundingClientRect();
    if (!rect) return null;
    const maxHeight = Math.min(MAX_EDITOR_HEIGHT, window.innerHeight - 24);
    const measuredSize = measureJsonEditorSize(value);
    const size = preferredEditorSize ?? measuredSize;
    const width = clamp(size.width, MIN_EDITOR_WIDTH, window.innerWidth - 24);
    const height = clamp(size.height, MIN_EDITOR_HEIGHT, maxHeight);
    return {
      left: calculateAnchoredLeft(rect, width),
      top: rect.bottom + height + 8 <= window.innerHeight
        ? rect.bottom + 4
        : Math.max(12, rect.top - height - 4),
      width,
      height,
      maxHeight
    };
  }

  function calculateAnchoredLeft(rect: DOMRect, width: number) {
    if (rect.left + width + 12 <= window.innerWidth) return Math.max(12, rect.left);
    if (rect.right - width >= 12) return rect.right - width;
    return Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
  }

  function startEditorResize(event: React.PointerEvent<HTMLButtonElement>) {
    if (!editorPosition) return;
    event.preventDefault();
    event.stopPropagation();
    resizingRef.current = true;
    const pointerId = event.pointerId;
    event.currentTarget.setPointerCapture(pointerId);
    const startX = event.clientX;
    const startY = event.clientY;
    const startWidth = editorPosition.width;
    const startHeight = editorPosition.height;
    const startLeft = editorPosition.left;
    const startTop = editorPosition.top;

    const handlePointerMove = (moveEvent: PointerEvent) => {
      const nextWidth = clamp(startWidth + moveEvent.clientX - startX, MIN_EDITOR_WIDTH, window.innerWidth - startLeft - 12);
      const nextHeight = clamp(startHeight + moveEvent.clientY - startY, MIN_EDITOR_HEIGHT, window.innerHeight - startTop - 12);
      preferredEditorSize = { width: nextWidth, height: nextHeight };
      setEditorPosition((current) => current ? { ...current, width: nextWidth, height: nextHeight } : current);
    };
    const handlePointerUp = () => {
      resizingRef.current = false;
      document.removeEventListener("pointermove", handlePointerMove);
      document.removeEventListener("pointerup", handlePointerUp);
    };

    document.addEventListener("pointermove", handlePointerMove);
    document.addEventListener("pointerup", handlePointerUp, { once: true });
  }

  function applyEditor() {
    if (kind === "sql") {
      suppressNextOpenRef.current = true;
      setRowData(editorValue.trim() ? editorValue : null);
      setEditorOpen(false);
      setEditorPosition(null);
      setError("");
      stopEditing({ nextRow: false });
      return;
    }
    try {
      const parsed: unknown = JSON.parse(editorValue);
      if (!Array.isArray(parsed) && (parsed === null || typeof parsed !== "object")) {
        setError("Enter a JSON object or array.");
        return;
      }
      if (kind === "array" && !Array.isArray(parsed)) {
        setError("This field expects a JSON array.");
        return;
      }
      if (kind === "object" && Array.isArray(parsed)) {
        setError("This field expects a JSON object.");
        return;
      }
      suppressNextOpenRef.current = true;
      setRowData(typeof rowData === "string" ? JSON.stringify(parsed) : parsed);
      setEditorOpen(false);
      setEditorPosition(null);
      setError("");
      stopEditing({ nextRow: false });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Invalid JSON.");
    }
  }

  function showPreview(pinned = false) {
    if (!prettyValue || (focus && !pinned)) return;
    const position = calculateTooltipPosition(pinned);
    if (position) setTooltipPosition(position);
  }

  function openDetail() {
    if (disabled || readOnly) showPreview(true);
    else openEditor();
  }

  async function copyFormattedValue(value: string) {
    if (!navigator.clipboard?.writeText) return;
    await navigator.clipboard.writeText(value);
  }

  function calculateTooltipPosition(pinned: boolean) {
    const rect = cellRef.current?.getBoundingClientRect();
    if (!rect || !prettyValue) return null;
    const { width, height } = measureJsonTooltipSize(prettyValue, pinned);
    return {
      left: calculateAnchoredLeft(rect, width),
      top: calculateAnchoredTop(rect, height),
      width,
      pinned
    };
  }

  return (
    <>
      <div
        ref={cellRef}
        className="metadata-text-cell metadata-structured-cell"
        onPointerDown={() => {
          activeBeforePointerDownRef.current = active;
        }}
        onClick={() => {
          if (activeBeforePointerDownRef.current) openDetail();
        }}
        onDoubleClick={(event) => {
          event.preventDefault();
          openDetail();
        }}
        onMouseEnter={() => {
          if (!active && !focus) showPreview(false);
        }}
        onMouseLeave={() => {
          if (!tooltipPosition?.pinned) setTooltipPosition(null);
        }}
      >
        <input
          className="dsg-input"
          aria-label={`${columnData.columnKey} ${structuredKindLabel(kind)} value`}
          readOnly
          tabIndex={-1}
          value={formatCellValue(rowData)}
        />
      </div>

      {tooltipPosition && prettyValue
        ? createPortal(
            <pre
              ref={tooltipRef}
              className={`metadata-json-tooltip ${tooltipPosition.pinned ? "pinned" : "hover"}`}
              style={{
                left: tooltipPosition.left,
                top: tooltipPosition.top,
                width: tooltipPosition.width
              }}
              role="tooltip"
              onMouseDown={(event) => event.stopPropagation()}
            >
              {tooltipPosition.pinned ? (
                <span className="metadata-json-tooltip-toolbar">
                  <span><Pin size={12} /> {structuredKindLabel(kind)} preview</span>
                  <span className="metadata-json-tooltip-actions">
                    <button type="button" aria-label={`Copy formatted ${structuredKindLabel(kind)} value`} onClick={() => void copyFormattedValue(prettyValue)}>
                      <Clipboard size={13} />
                    </button>
                    <button type="button" aria-label={`Close ${structuredKindLabel(kind)} preview`} onClick={() => setTooltipPosition(null)}>
                      <X size={13} />
                    </button>
                  </span>
                </span>
              ) : null}
              <code>{highlightedPrettyValue}</code>
            </pre>,
            document.body
          )
        : null}

      {editorOpen && editorPosition
        ? createPortal(
              <section
                ref={editorRef}
                className="metadata-json-editor metadata-json-editor-popover"
                style={{
                  left: editorPosition.left,
                  top: editorPosition.top,
                  width: editorPosition.width,
                  height: editorPosition.height,
                  maxHeight: editorPosition.maxHeight
                }}
                role="dialog"
                aria-labelledby="metadata-json-editor-title"
                onMouseDown={(event) => event.stopPropagation()}
              >
                <header>
                  <div>
                    <h2 id="metadata-json-editor-title">Edit {columnData.columnKey}</h2>
                    <span>{structuredKindLabel(kind)}</span>
                  </div>
                  <div className="metadata-json-editor-actions">
                    <button type="button" className="icon-button" aria-label={`Copy formatted ${structuredKindLabel(kind)} value`} onClick={() => void copyFormattedValue(editorValue)}>
                      <Clipboard size={16} />
                    </button>
                    <button type="button" className="icon-button" aria-label="Close JSON editor" onClick={closeEditor}>
                      <X size={16} />
                    </button>
                  </div>
                </header>
                <div className="metadata-json-editor-surface">
                  <pre aria-hidden="true">{highlightStructuredValue(editorValue, kind)}</pre>
                  <textarea
                    ref={textareaRef}
                    value={editorValue}
                    spellCheck={false}
                    aria-invalid={Boolean(error)}
                    onChange={(event) => {
                      setEditorValue(event.target.value);
                      if (error) setError("");
                    }}
                    onScroll={(event) => {
                      const surface = event.currentTarget.previousElementSibling;
                      if (surface instanceof HTMLElement) {
                        surface.scrollTop = event.currentTarget.scrollTop;
                        surface.scrollLeft = event.currentTarget.scrollLeft;
                      }
                    }}
                    onKeyDown={(event) => {
                      event.stopPropagation();
                      if (event.key === "Escape") {
                        event.preventDefault();
                        closeEditor();
                      } else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                        event.preventDefault();
                        applyEditor();
                      }
                    }}
                  />
                </div>
                <footer>
                  <span className={error ? "metadata-json-editor-error" : "metadata-json-editor-hint"} role={error ? "alert" : undefined}>
                    {error || "Esc to cancel | Ctrl+Enter to apply"}
                  </span>
                  <div>
                    <button type="button" className="secondary-button" onClick={closeEditor}>Cancel</button>
                    <button type="button" className="primary-button" onClick={applyEditor}>
                      <Check size={15} />
                      Apply
                    </button>
                  </div>
                </footer>
                <button
                  type="button"
                  className="metadata-json-editor-resize"
                  aria-label="Resize JSON editor"
                  onPointerDown={startEditorResize}
                />
              </section>
            ,
            document.body
          )
        : null}
    </>
  );
};

function highlightJson(value: string) {
  const tokens = value.match(/"(?:\\.|[^"\\])*"(?=\s*:)|"(?:\\.|[^"\\])*"|[-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\b(?:true|false|null)\b|[{}\[\],:]/g);
  if (!tokens) return value;

  const nodes: React.ReactNode[] = [];
  let cursor = 0;

  tokens.forEach((token, index) => {
    const start = value.indexOf(token, cursor);
    if (start < cursor) return;
    if (start > cursor) nodes.push(value.slice(cursor, start));
    nodes.push(
      <span key={`${start}-${index}`} className={`json-token ${jsonTokenClass(token, value.slice(start + token.length))}`}>
        {token}
      </span>
    );
    cursor = start + token.length;
  });

  if (cursor < value.length) nodes.push(value.slice(cursor));
  return nodes;
}

function highlightSql(value: string) {
  const tokens = value.match(/'(?:''|[^'])*'|"(?:\\.|[^"\\])*"|--.*?$|\/\*[\s\S]*?\*\/|\b(?:select|from|where|join|left|right|full|inner|outer|on|and|or|not|case|when|then|else|end|as|with|group|by|order|having|limit|offset|union|all|distinct|insert|update|delete|merge|into|values|set|is|null|in|exists|between|like|cast|coalesce|partition|over)\b|[-]?\d+(?:\.\d+)?|[(),.=<>+\-*/]/gim);
  if (!tokens) return value;

  const nodes: React.ReactNode[] = [];
  let cursor = 0;

  tokens.forEach((token, index) => {
    const start = value.indexOf(token, cursor);
    if (start < cursor) return;
    if (start > cursor) nodes.push(value.slice(cursor, start));
    nodes.push(
      <span key={`${start}-${index}`} className={`sql-token ${sqlTokenClass(token)}`}>
        {token}
      </span>
    );
    cursor = start + token.length;
  });

  if (cursor < value.length) nodes.push(value.slice(cursor));
  return nodes;
}

export function formatPrettySql(value: string) {
  const normalized = value
    .replace(/\s+/g, " ")
    .replace(/\s*,\s*/g, ", ")
    .trim();
  if (!normalized) return "";
  return normalized
    .replace(/\b(with)\b/gi, "\n$1")
    .replace(/\b(select)\b/gi, "\n$1")
    .replace(/\b(from)\b/gi, "\n$1")
    .replace(/\b((?:left|right|full|inner|outer|cross)\s+join|join)\b/gi, "\n$1")
    .replace(/\b(where)\b/gi, "\n$1")
    .replace(/\b(and|or)\b/gi, "\n  $1")
    .replace(/\b(group\s+by)\b/gi, "\n$1")
    .replace(/\b(having)\b/gi, "\n$1")
    .replace(/\b(order\s+by)\b/gi, "\n$1")
    .replace(/\b(limit|offset|union all|union)\b/gi, "\n$1")
    .replace(/,\s+(?=(?:[^']*'[^']*')*[^']*$)/g, ",\n  ")
    .replace(/\s+,\s+/g, ", ")
    .trim();
}

export function highlightStructuredValue(value: string, kind: StructuredCellKind | null) {
  return kind === "sql" ? highlightSql(value) : highlightJson(value);
}

function sqlTokenClass(token: string) {
  if (/^(--|\/\*)/.test(token)) return "sql-comment";
  if (/^['"]/.test(token)) return "sql-string";
  if (/^-?\d/.test(token)) return "sql-number";
  if (/^[a-z_]+$/i.test(token)) return "sql-keyword";
  return "sql-punctuation";
}

function defaultStructuredValue(kind: StructuredCellKind) {
  if (kind === "array") return "[]";
  if (kind === "object") return "{}";
  return "";
}

function structuredKindLabel(kind: StructuredCellKind | null) {
  if (kind === "sql") return "SQL";
  if (kind === "array") return "JSON array";
  return "JSON object";
}

function jsonTokenClass(token: string, afterToken: string) {
  if (/^"/.test(token) && afterToken.trimStart().startsWith(":")) return "json-key";
  if (/^"/.test(token)) return "json-string";
  if (/^-?\d/.test(token)) return "json-number";
  if (token === "true" || token === "false") return "json-boolean";
  if (token === "null") return "json-null";
  return "json-punctuation";
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(value, max));
}

function calculateAnchoredTop(rect: DOMRect, height: number) {
  return rect.bottom + height + 8 <= window.innerHeight
    ? rect.bottom + 4
    : Math.max(12, rect.top - height - 4);
}

function measureJsonEditorSize(value: string): EditorSize {
  const ruler = getJsonContentRuler();
  if (!ruler) return { width: DEFAULT_EDITOR_WIDTH, height: DEFAULT_EDITOR_HEIGHT };
  ruler.textContent = value || " ";
  const rect = ruler.getBoundingClientRect();
  return {
    width: clamp(Math.ceil(rect.width), MIN_EDITOR_WIDTH, MAX_EDITOR_WIDTH),
    height: clamp(Math.ceil(rect.height + 116), MIN_EDITOR_HEIGHT, MAX_EDITOR_HEIGHT)
  };
}

function getJsonContentRuler() {
  if (jsonContentRuler !== undefined) return jsonContentRuler;
  if (typeof document === "undefined") {
    jsonContentRuler = null;
    return jsonContentRuler;
  }
  jsonContentRuler = document.createElement("pre");
  jsonContentRuler.className = "metadata-json-content-ruler";
  jsonContentRuler.setAttribute("aria-hidden", "true");
  document.body.appendChild(jsonContentRuler);
  return jsonContentRuler;
}

function measureJsonTooltipSize(value: string, pinned: boolean): EditorSize {
  const viewportWidth = window.innerWidth - 24;
  if (pinned) {
    return {
      width: Math.min(560, viewportWidth),
      height: Math.min(
        420,
        Math.max(72, value.split("\n").length * 16 + 64)
      )
    };
  }

  const ruler = getJsonTooltipRuler();
  if (!ruler) {
    return {
      width: Math.min(440, viewportWidth),
      height: Math.min(260, Math.max(72, value.split("\n").length * 16 + 20))
    };
  }

  ruler.style.maxWidth = `${Math.min(440, viewportWidth)}px`;
  ruler.textContent = value || " ";
  const rect = ruler.getBoundingClientRect();
  return {
    width: clamp(Math.ceil(rect.width), Math.min(180, viewportWidth), viewportWidth),
    height: clamp(Math.ceil(rect.height), 36, Math.min(260, window.innerHeight - 24))
  };
}

function getJsonTooltipRuler() {
  if (jsonTooltipRuler !== undefined) return jsonTooltipRuler;
  if (typeof document === "undefined") {
    jsonTooltipRuler = null;
    return jsonTooltipRuler;
  }
  jsonTooltipRuler = document.createElement("pre");
  jsonTooltipRuler.className = "metadata-json-tooltip-ruler";
  jsonTooltipRuler.setAttribute("aria-hidden", "true");
  document.body.appendChild(jsonTooltipRuler);
  return jsonTooltipRuler;
}
