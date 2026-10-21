import type { LineageAsset } from "./api/domainTypes";
import { assetIconKind } from "./formatPresentation";
import type { FormatIconKind } from "./formatPresentation";

const PATH_SEPARATOR = /[\\/]+/;

export interface AssetPresentation {
  locator: string;
  connection: string;
  badge: string;
  iconKind: AssetIconKind;
  fullIdentity: string;
}

export type AssetIconKind = FormatIconKind;

export { assetIconKind };

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
