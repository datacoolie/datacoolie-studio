export type FormatIconKind =
  | "api"
  | "archive"
  | "avro"
  | "binary"
  | "code"
  | "csv"
  | "database"
  | "delta"
  | "excel"
  | "file"
  | "iceberg"
  | "json"
  | "orc"
  | "parquet"
  | "python"
  | "sql"
  | "table"
  | "text"
  | "xml"
  | "yaml";

export function canonicalFormat(value: unknown): string {
  if (typeof value !== "string") return "";
  const text = value.trim().toLowerCase();
  if (["", "unknown", "null", "none"].includes(text)) return "";
  const aliases: Record<string, string> = { deltalake: "delta", apacheiceberg: "iceberg", apacheparquet: "parquet", apacheavro: "avro", apacheorc: "orc" };
  return aliases[text.replace(/[_\s-]/g, "")] ?? text;
}

export function assetIconKind(value: string): FormatIconKind {
  const normalized = canonicalFormat(value).replace(/[_\s-]/g, "");
  if (normalized === "api" || normalized === "http" || normalized === "rest") return "api";
  if (normalized === "python" || normalized === "pythonfunction") return "python";
  if (normalized === "function" || normalized === "code") return "code";
  if (normalized === "sql" || normalized === "sqlquery") return "sql";
  if (["database", "jdbc", "odbc"].includes(normalized)) return "database";
  if (normalized === "delta" || normalized === "deltalake") return "delta";
  if (normalized === "iceberg" || normalized === "apacheiceberg") return "iceberg";
  if (normalized === "table" || normalized === "lakehouse") return "table";
  if (normalized === "parquet" || normalized === "apacheparquet") return "parquet";
  if (normalized === "avro" || normalized === "apacheavro") return "avro";
  if (normalized === "orc" || normalized === "apacheorc") return "orc";
  if (normalized === "binary") return "binary";
  if (["json", "jsonl", "ndjson"].includes(normalized)) return "json";
  if (normalized === "csv") return "csv";
  if (["excel", "xlsx", "xls", "spreadsheet"].includes(normalized)) return "excel";
  if (["zip", "wheel", "whl", "archive"].includes(normalized)) return "archive";
  if (normalized === "xml") return "xml";
  if (normalized === "yaml" || normalized === "yml") return "yaml";
  if (normalized === "txt" || normalized === "text") return "text";
  return "file";
}
