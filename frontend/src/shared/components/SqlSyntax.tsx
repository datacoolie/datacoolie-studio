import type { ReactNode } from "react";

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

export function highlightSql(value: string): ReactNode {
  const tokens = value.match(/'(?:''|[^'])*'|"(?:\\.|[^"\\])*"|--.*?$|\/\*[\s\S]*?\*\/|\b(?:select|from|where|join|left|right|full|inner|outer|on|and|or|not|case|when|then|else|end|as|with|group|by|order|having|limit|offset|union|all|distinct|insert|update|delete|merge|into|values|set|is|null|in|exists|between|like|cast|coalesce|partition|over)\b|[-]?\d+(?:\.\d+)?|[(),.=<>+\-*/]/gim);
  if (!tokens) return value;

  const nodes: ReactNode[] = [];
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

function sqlTokenClass(token: string) {
  if (/^(--|\/\*)/.test(token)) return "sql-comment";
  if (/^['"]/.test(token)) return "sql-string";
  if (/^-?\d/.test(token)) return "sql-number";
  if (/^[a-z_]+$/i.test(token)) return "sql-keyword";
  return "sql-punctuation";
}
