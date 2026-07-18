import { ChevronLeft, ChevronRight, Copy, Maximize2, Minimize2, WrapText } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import type { ReferenceSourceMatch } from "../../shared/api/types";
import { highlightedSourceCode, sourceCodeLanguage } from "./sourceCode";

interface SourceCodeViewerProps {
  content: string;
  language: string | null | undefined;
  matches?: ReferenceSourceMatch[];
  className?: string;
  ariaLabel: string;
  defaultWrapped?: boolean;
  showHeightControl?: boolean;
  toolbarLeading?: ReactNode;
}

export function SourceCodeViewer({ content, language, matches = [], className = "", ariaLabel, defaultWrapped = false, showHeightControl = true, toolbarLeading }: SourceCodeViewerProps) {
  const [wrapped, setWrapped] = useState(defaultWrapped);
  const [expanded, setExpanded] = useState(true);
  const [copied, setCopied] = useState(false);
  const [activeMatch, setActiveMatch] = useState(0);
  const [rangeHighlightAvailable, setRangeHighlightAvailable] = useState(true);
  const codeRef = useRef<HTMLElement | null>(null);
  const highlightName = `reference-source-${useId().replace(/[^a-z0-9_-]/giu, "")}`;
  const match = matches[activeMatch] || null;
  const fallbackHighlightStyle = match && !rangeHighlightAvailable
    ? { "--source-highlight-offset": `${Math.max(match.line - 1, 0) * 1.55}em` } as CSSProperties
    : undefined;

  useEffect(() => {
    setActiveMatch(0);
  }, [content, matches.length]);

  useEffect(() => {
    setWrapped(defaultWrapped);
  }, [content, defaultWrapped]);

  useLayoutEffect(() => {
    const code = codeRef.current;
    const customHighlights = getCustomHighlightRegistry();
    const customHighlight = getCustomHighlightConstructor();
    if (!code || !match || !customHighlights || !customHighlight) {
      setRangeHighlightAvailable(Boolean(!match || (customHighlights && customHighlight)));
      return undefined;
    }
    const range = sourceRange(code, content, match);
    if (!range) {
      setRangeHighlightAvailable(false);
      return undefined;
    }
    customHighlights.set(highlightName, new customHighlight(range));
    setRangeHighlightAvailable(true);
    requestAnimationFrame(() => scrollRangeIntoView(range, code));
    return () => {
      customHighlights.delete(highlightName);
    };
  }, [content, highlightName, match]);

  async function copySource() {
    await navigator.clipboard?.writeText(content);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  }

  return (
    <div className={`source-code-viewer${className ? ` ${className}` : ""}`}>
      <style>{`::highlight(${highlightName}) { background: rgba(246, 196, 79, 0.45); color: inherit; }`}</style>
      <div className="source-code-toolbar">
        <div className="source-code-toolbar-leading">
          {toolbarLeading ?? <span>{sourceCodeLanguage(language) || "text"}</span>}
        </div>
        <div className="source-code-actions">
          {matches.length > 1 ? (
            <>
              <button className="icon-action small" type="button" title="Previous match" aria-label="Previous match" onClick={() => setActiveMatch((value) => (value + matches.length - 1) % matches.length)}>
                <ChevronLeft size={13} />
              </button>
              <small>{activeMatch + 1} / {matches.length}</small>
              <button className="icon-action small" type="button" title="Next match" aria-label="Next match" onClick={() => setActiveMatch((value) => (value + 1) % matches.length)}>
                <ChevronRight size={13} />
              </button>
            </>
          ) : null}
          <button className="icon-action small" type="button" title={wrapped ? "No wrap" : "Wrap"} aria-label={wrapped ? "No wrap" : "Wrap"} onClick={() => setWrapped((value) => !value)}>
            <WrapText size={13} />
          </button>
          {showHeightControl ? (
            <button className="icon-action small" type="button" title={expanded ? "Compact height" : "Expand height"} aria-label={expanded ? "Compact height" : "Expand height"} onClick={() => setExpanded((value) => !value)}>
              {expanded ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
            </button>
          ) : null}
          <button className={`icon-action small${copied ? " copied" : ""}`} type="button" title={copied ? "Copied" : "Copy"} aria-label={copied ? "Copied" : "Copy"} onClick={() => void copySource()}>
            <Copy size={13} />
          </button>
        </div>
      </div>
      <pre className={`source-code-block${wrapped ? " is-wrapped" : ""}${expanded ? " is-expanded" : ""}${fallbackHighlightStyle ? " has-location-highlight" : ""}`} style={fallbackHighlightStyle} aria-label={ariaLabel}>
        <code ref={codeRef} className={`hljs language-${sourceCodeLanguage(language) || "text"}`} dangerouslySetInnerHTML={{ __html: highlightedSourceCode(content, language) }} />
      </pre>
      {match && !rangeHighlightAvailable ? <small className="source-code-fallback">Detection location: line {match.line}, column {match.column + 1}</small> : null}
    </div>
  );
}

function sourceRange(root: HTMLElement, content: string, match: ReferenceSourceMatch) {
  const start = offsetForPosition(content, match.line, match.column);
  const end = offsetForPosition(content, match.end_line, match.end_column);
  if (start === null || end === null || end < start) return null;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: Array<{ node: Text; start: number; end: number }> = [];
  let offset = 0;
  for (let current = walker.nextNode(); current; current = walker.nextNode()) {
    const node = current as Text;
    nodes.push({ node, start: offset, end: offset + node.data.length });
    offset += node.data.length;
  }
  const startNode = nodes.find((item) => start >= item.start && start <= item.end);
  const endNode = nodes.find((item) => end >= item.start && end <= item.end);
  if (!startNode || !endNode) return null;
  const range = document.createRange();
  range.setStart(startNode.node, start - startNode.start);
  range.setEnd(endNode.node, end - endNode.start);
  return range;
}

function offsetForPosition(content: string, line: number, column: number) {
  if (line < 1 || column < 0) return null;
  let offset = 0;
  for (let currentLine = 1; currentLine < line; currentLine += 1) {
    const nextLine = content.indexOf("\n", offset);
    if (nextLine < 0) return null;
    offset = nextLine + 1;
  }
  return Math.min(offset + column, content.length);
}

function scrollRangeIntoView(range: Range, code: HTMLElement) {
  const source = code.closest("pre");
  if (!source) return;
  const target = range.getBoundingClientRect();
  const container = source.getBoundingClientRect();
  if (target.top < container.top || target.bottom > container.bottom) {
    source.scrollTop += target.top - container.top - source.clientHeight / 2;
  }
}

type CustomHighlightRegistry = { set(name: string, highlight: unknown): void; delete(name: string): boolean };
type CustomHighlightConstructor = new (range: Range) => unknown;

function getCustomHighlightRegistry() {
  return (CSS as unknown as { highlights?: CustomHighlightRegistry }).highlights || null;
}

function getCustomHighlightConstructor() {
  return (window as Window & { Highlight?: CustomHighlightConstructor }).Highlight || null;
}
