export function parseCellText(value: string) {
  const normalized = value.trim();
  if (!normalized) return null;
  if (normalized.toLowerCase() === "true") return true;
  if (normalized.toLowerCase() === "false") return false;
  return value;
}

export function formatCellValue(value: unknown) {
  if (value == null) return "";
  if (Array.isArray(value) || typeof value === "object") return JSON.stringify(value);
  return String(value);
}

const structuredArrayFields = new Set([
  "additional_columns",
  "deduplicate_columns",
  "latest_data_columns",
  "merge_keys",
  "partition_columns",
  "schema_hints",
  "source_watermark_columns",
  "transform_drop_columns",
  "transform_hash_columns",
  "transform_masking_rules",
  "transform_select_columns",
  "transform_value_rules"
]);

const structuredObjectFields = new Set([
  "transform_rename_columns"
]);

const structuredSqlFields = new Set([
  "source_query",
  "source_filter_expression",
  "transform_filter_expression"
]);

export type StructuredCellKind = "array" | "object" | "sql";

export function structuredCellKind(columnKey: string, value: unknown): StructuredCellKind | null {
  if (structuredSqlFields.has(columnKey)) return "sql";
  const parsed = parseStructuredValue(value);
  if (parsed) return Array.isArray(parsed) ? "array" : "object";
  if (structuredArrayFields.has(columnKey)) return "array";
  if (structuredObjectFields.has(columnKey)) return "object";
  if (columnKey === "configure" || columnKey.endsWith("_configure")) return "object";
  return null;
}

export function parseStructuredValue(value: unknown): Record<string, unknown> | unknown[] | null {
  if (Array.isArray(value)) return value;
  if (value && typeof value === "object") return value as Record<string, unknown>;
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const parsed: unknown = JSON.parse(value);
    return Array.isArray(parsed) || (parsed !== null && typeof parsed === "object")
      ? parsed as Record<string, unknown> | unknown[]
      : null;
  } catch {
    return null;
  }
}

export function formatPrettyStructuredValue(value: unknown) {
  const parsed = parseStructuredValue(value);
  return parsed ? JSON.stringify(parsed, null, 2) : "";
}
