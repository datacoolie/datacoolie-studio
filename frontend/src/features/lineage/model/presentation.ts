import type { LineageAsset } from "../../../shared/api/types";

const PATH_SEPARATOR = /[\\/]+/;

export interface AssetPresentation {
  locator: string;
  connection: string;
  badge: string;
  iconKind: AssetIconKind;
  fullIdentity: string;
}

export type AssetIconKind =
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

export function presentLineageAsset(node: LineageAsset): AssetPresentation {
  const locator = friendlyAssetName(node);
  const connection = node.connection_name || "unknown connection";
  const format = node.format || node.endpoint_kind || node.connection_type || node.identity_type || "asset";
  const badge = format.replace(/_/g, " ").toUpperCase();
  return {
    locator,
    connection,
    badge,
    iconKind: assetIconKind(format),
    fullIdentity: [connection, node.path || qualifiedTable(node) || locator].filter(Boolean).join(" · ")
  };
}

export function lineageNodeSearchValues(node: LineageAsset): string[] {
  const presentation = presentLineageAsset(node);
  return [
    node.id,
    presentation.locator,
    presentation.connection,
    presentation.badge,
    presentation.fullIdentity,
    node.label,
    node.path,
    node.table,
    node.schema_name,
    node.catalog,
    node.database,
    node.query,
    node.python_function
  ].filter(isPresent);
}

function qualifiedTable(node: LineageAsset) {
  if (!node.table) return null;
  return [node.catalog, node.database, node.schema_name, node.table].filter(Boolean).join(".");
}

function friendlyAssetName(node: LineageAsset) {
  if (node.python_function) return node.python_function.split(".").pop() || node.python_function;
  if (node.query) return "SQL query";
  if (node.endpoint_kind === "api" || node.connection_type === "api" || node.format === "api") {
    return pathBasename(node.path) || node.label || "API endpoint";
  }
  if (node.table) return node.table;
  return pathBasename(node.path)
    || pathBasename(node.endpoint_locator)
    || node.display_name
    || node.display_label
    || node.label
    || "Unknown asset";
}

function pathBasename(path: string | null | undefined) {
  if (!path) return null;
  const parts = path.split(PATH_SEPARATOR).filter((part) => part && part !== ".");
  return parts.at(-1) || path;
}

export function assetIconKind(value: string): AssetIconKind {
  const normalized = value.toLowerCase().replace(/[_\s-]/g, "");
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

export function referenceTypeAssetType(referenceType: string): string {
  if (referenceType === "table_reference") return "table";
  if (referenceType === "path_reference") return "path";
  if (referenceType === "api_endpoint_reference") return "api";
  return "unresolved";
}

export function assetTypeTone(assetType: LineageAsset["asset_type"] | string): string {
  if (assetType === "table") return "table";
  if (assetType === "path") return "path";
  if (assetType === "sql_query") return "sql";
  if (assetType === "python_function") return "python";
  if (assetType === "api") return "api";
  if (assetType === "unresolved") return "unresolved";
  return "default";
}

export function assetTypeIconId(assetType: LineageAsset["asset_type"] | string): string {
  if (assetType === "table") return "bx:table";
  if (assetType === "path") return "octicon:rel-file-path-16";
  if (assetType === "sql_query") return "carbon:sql";
  if (assetType === "python_function") return "gravity-ui:function";
  if (assetType === "api") return "gcp:api";
  if (assetType === "unresolved") return "carbon:unknown";
  return "carbon:data-base";
}

function isPresent(value: string | null | undefined): value is string {
  return Boolean(value);
}
