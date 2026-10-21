import hljs from "highlight.js/lib/core";
import jsonLanguage from "highlight.js/lib/languages/json";
import pythonLanguage from "highlight.js/lib/languages/python";
import sqlLanguage from "highlight.js/lib/languages/sql";
import "highlight.js/styles/vs2015.css";

hljs.registerLanguage("python", pythonLanguage);
hljs.registerLanguage("sql", sqlLanguage);
hljs.registerLanguage("json", jsonLanguage);

export function highlightedSourceCode(content: string, language: string | null | undefined) {
  const normalized = sourceCodeLanguage(language);
  if (!normalized) return escapeHtml(content);
  return hljs.highlight(content, { language: normalized, ignoreIllegals: true }).value;
}

export function sourceCodeLanguage(language: string | null | undefined) {
  const normalized = String(language || "").toLowerCase();
  if (normalized.includes("python")) return "python";
  if (normalized.includes("sql")) return "sql";
  if (normalized.includes("json")) return "json";
  return null;
}

function escapeHtml(content: string) {
  return content
    .replace(/&/gu, "&amp;")
    .replace(/</gu, "&lt;")
    .replace(/>/gu, "&gt;")
    .replace(/"/gu, "&quot;")
    .replace(/'/gu, "&#39;");
}
