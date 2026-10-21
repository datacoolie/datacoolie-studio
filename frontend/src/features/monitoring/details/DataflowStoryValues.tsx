import { Check, Copy } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { isLosslessJsonNumber, parseRawRecordJson } from "../../../shared/api/monitoringAdapters";
import { copyToClipboard, formatListBlock, safeJsonStringify } from "./detailPrimitives";
import { StoryDisclosure } from "./DataflowStoryPrimitives";
import { storyIssues } from "./dataflowStoryModel";
import { highlightSql } from "../../../shared/components/SqlSyntax";
import { highlightJson } from "./detailPrimitives";
import { Help } from "../../../shared/components/Help";

const TAGS: Record<string, string> = { __date__: "date", __datetime__: "datetime", __time__: "time", __decimal__: "decimal", __binary__: "binary" };
export type StoryField = { label: string; value: unknown; path: string; content?: ReactNode; text?: boolean; kind?: "sql" | "json" };

export function StoryCoverage({ diagnostics, path }: { diagnostics?: unknown[] | null; path: string }) {
  const issues = storyIssues(diagnostics, path);
  if (!issues.length) return null;
  return <Help className="dataflow-story-coverage" title={issues.map(issue => `${issue.field}: ${issue.reason}`).join("\n")}>{[...new Set(issues.map(issue => issue.reason))].join(" · ")}</Help>;
}

/** Exact native kinds are classified before scalar metric formatting. */
export function storyValuePreview(value: unknown, depth = 0): { text: string; complex: boolean } {
  if (value === undefined) return { text: "unk", complex: false };
  if (value === null) return { text: "null", complex: false };
  if (isLosslessJsonNumber(value)) return { text: value.value.slice(0,160) + (value.value.length > 160 ? "…" : ""), complex: value.value.length > 160 };
  if (typeof value === "string") return { text: JSON.stringify(value.slice(0, 160)) + (value.length > 160 ? "…" : ""), complex: value.length > 160 };
  if (typeof value !== "object") return { text: String(value), complex: false };
  if (!Array.isArray(value)) {
    const keys = Object.keys(value);
    if (keys.length === 1 && TAGS[keys[0]] && typeof (value as Record<string, unknown>)[keys[0]] === "string") {
      const raw = (value as Record<string, string>)[keys[0]];
      return { text: `${TAGS[keys[0]]}: ${raw.slice(0, 160)}${raw.length > 160 ? "…" : ""}`, complex: raw.length > 160 };
    }
  }
  const entries = Array.isArray(value) ? value : Object.keys(value);
  if (!entries.length) return { text: Array.isArray(value) ? "[]" : "{}", complex: false };
  if (depth >= 2 || entries.length > 4) return { text: `${entries.length} ${Array.isArray(value) ? "items" : "fields"}`, complex: true };
  const previews = Array.isArray(value)
    ? value.map(item => storyValuePreview(item, depth + 1))
    : entries.map(key => { const item = storyValuePreview((value as Record<string, unknown>)[String(key)], depth + 1); return { ...item, text: `${JSON.stringify(String(key).slice(0, 80))}: ${item.text}`, complex: item.complex || String(key).length > 80 }; });
  const text = Array.isArray(value) ? `[${previews.map(item => item.text).join(", ")}]` : `{${previews.map(item => item.text).join(", ")}}`;
  return { text: text.slice(0, 240) + (text.length > 240 ? "…" : ""), complex: text.length > 240 || previews.some(item => item.complex) };
}

export function StoryExactValue({ value, text = false, kind }: { value: unknown; text?: boolean; kind?: "sql" | "json" }) {
  // Only short encoded collections are decoded to choose immediate boxes. Long
  // config/SQL strings are left untouched until their disclosure is opened.
  const inlineValue = useMemo(() => kind === "json" && typeof value === "string" && value.length <= 240 ? parseRawRecordJson(value) ?? value : value, [value, kind]);
  const preview = useMemo(() => storyValuePreview(inlineValue), [inlineValue]);
  const structured = kind !== undefined || (value !== null && typeof value === "object" && !isLosslessJsonNumber(value));
  const display = text && typeof inlineValue === "string" && inlineValue.trim() ? inlineValue.slice(0, 160) + (inlineValue.length > 160 ? "…" : "") : preview.text;
  if (structured) return preview.complex || kind === "sql" || (typeof value === "string" && value.length > 240)
    ? <div className="dataflow-story-code-disclosure"><StoryDisclosure className="dataflow-story-value-disclosure" title={<span>View {(kind ?? "json").toUpperCase()}</span>}>{() => <StoryExpandedValue value={value} kind={kind ?? "json"} />}</StoryDisclosure></div>
    : <StoryExpandedValue value={value} kind={kind ?? "json"} />;
  if (preview.complex) return <StoryDisclosure className="dataflow-story-value-disclosure" title={<span>{display}</span>}>{() => <StoryExpandedValue value={value} />}</StoryDisclosure>;
  return <span className="dataflow-story-exact-value">{display}</span>;
}

export function StoryExpandedValue({ value, kind, showType = true, compact = false }: { value: unknown; kind?: "sql" | "json"; showType?: boolean; compact?: boolean }) {
  const [copied,setCopied] = useState(false);
  const formatted = useMemo(() => {
    if (value === undefined) return "unk";
    if (compact && kind === "json") return formatListBlock(value);
    if (kind === "sql") return typeof value==="string" ? value : safeJsonStringify(value);
    if (kind === "json" && typeof value === "string") {
      const parsed=parseRawRecordJson(value);
      if (parsed !== null || value.trim()==="null") return safeJsonStringify(parsed);
      return value;
    }
    return safeJsonStringify(value);
  },[compact,kind,value]);
  return <div className={`dataflow-story-code-box monitoring-code-box-${kind ?? "json"}${compact ? " is-compact-list" : ""}`}>
    <div className={`dataflow-story-code-toolbar${showType ? "" : " is-copy-only"}`}>{showType ? <span>{(kind ?? "json").toUpperCase()}</span> : null}<Help as="button" type="button" className="icon-action small" title={`Copy ${(kind ?? "json").toUpperCase()}`} aria-label={`Copy ${(kind ?? "json").toUpperCase()}`} onClick={()=>{void copyToClipboard(kind && typeof value==="string" ? value : safeJsonStringify(value),setCopied);}}>{copied?<Check size={12}/>:<Copy size={12}/>}</Help></div>
    <pre className={`monitoring-code-block ${kind==="sql"?"sql-syntax-light":"monitoring-json-light"}`}>{kind==="sql"?highlightSql(formatted):highlightJson(formatted)}</pre>
  </div>;
}

export function StoryFields({ fields, diagnostics, className = "", expandStructured = false }: { fields: StoryField[]; diagnostics?: unknown[] | null; className?: string; expandStructured?: boolean }) {
  return <dl className={`dataflow-story-fields ${className}`}>{fields.map(field => {
    const structured=Boolean(field.kind || field.value !== null && typeof field.value==="object" && !isLosslessJsonNumber(field.value));
    return <div key={field.path} data-field-path={field.path} className={structured ? "is-structured" : undefined}><dt>{field.label} <StoryCoverage diagnostics={diagnostics} path={field.path} /></dt><dd>{field.content ?? (expandStructured && structured ? <StoryExpandedValue value={field.value} kind={field.kind ?? "json"} showType={false} /> : <StoryExactValue value={field.value} text={field.text} kind={field.kind} />)}</dd></div>;
  })}</dl>;
}
