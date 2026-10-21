import type { ReactNode } from "react";
import { highlightSql } from "../components/SqlSyntax";
import type { StructuredCellKind } from "./metadataCellValues";

export function highlightStructuredValue(value: string, kind: StructuredCellKind | null) {
  return kind === "sql" ? highlightSql(value) : highlightJson(value);
}

function highlightJson(value: string): ReactNode {
  const tokens = value.match(/"(?:\\.|[^"\\])*"(?=\s*:)|"(?:\\.|[^"\\])*"|[-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\b(?:true|false|null)\b|[{}\[\],:]/g);
  if (!tokens) return value;

  const nodes: ReactNode[] = [];
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

function jsonTokenClass(token: string, afterToken: string) {
  if (/^"/.test(token) && afterToken.trimStart().startsWith(":")) return "json-key";
  if (/^"/.test(token)) return "json-string";
  if (/^-?\d/.test(token)) return "json-number";
  if (token === "true" || token === "false") return "json-boolean";
  if (token === "null") return "json-null";
  return "json-punctuation";
}
