/** Pure name/context rules shared by Assets and endpoint consumers. */
export interface AssetNamingInput {
  id: string;
  display_name: string;
  full_identity: string;
  asset_type: string;
  friendly_name?: string | null;
  table?: string | null;
  path?: string | null;
  catalog?: string | null;
  database?: string | null;
  schema_name?: string | null;
  query?: string | null;
  python_function?: string | null;
  identifiers?: readonly unknown[] | null;
}

export function assetFriendlyAlias(asset: AssetNamingInput, fallback: string, options?: { qualifyTableWithSchema?: boolean }) {
  if (options?.qualifyTableWithSchema && asset.asset_type === "table" && asset.table && asset.schema_name?.trim()) {
    return `${asset.schema_name.trim()}.${asset.table}`;
  }
  if (asset.friendly_name?.trim()) return asset.friendly_name.trim();
  if (asset.asset_type === "sql_query" || asset.query) return sqlQueryAlias(asset, fallback);
  if (asset.asset_type === "python_function" || asset.python_function) return pythonFunctionAlias(asset, fallback);
  if (asset.asset_type === "api") {
    const endpointAlias = apiEndpointAlias(asset);
    if (endpointAlias) return endpointAlias;
  }
  if (asset.asset_type === "table" && asset.table) return asset.table;
  if (asset.asset_type === "path" && asset.path) return compactAssetPath(asset.path);
  if (asset.table) return asset.table;
  if (asset.path) return compactAssetPath(asset.path);
  return asset.friendly_name || asset.display_name || fallback || asset.id;
}

export function assetLocatorDetail(asset: AssetNamingInput, alias: string) {
  const hasAlias = usesFriendlyAlias(asset, alias);
  if (asset.asset_type === "python_function" || asset.python_function) return functionContext(asset, alias, hasAlias);
  if (asset.asset_type === "sql_query" || asset.query) return asset.query ? sqlQueryContext(asset.query) : "";
  if (asset.asset_type === "api") {
    const endpoint = apiEndpointLabel(asset);
    if (endpoint) return hasAlias ? endpoint : apiEndpointContext(endpoint, alias);
  }
  if (asset.asset_type === "table" && asset.table) return tableContext(asset);
  if (asset.asset_type === "path" && asset.path) return pathContext(asset.path, alias);
  if (asset.table) return tableContext(asset);
  if (asset.path) return pathContext(asset.path, alias);
  return nonDuplicateText(asset.display_name || asset.full_identity || "", alias);
}

function tableContext(asset: AssetNamingInput) {
  return [asset.catalog, asset.database, asset.schema_name].filter(Boolean).join(".");
}

function apiEndpointAlias(asset: AssetNamingInput) {
  const endpoint = apiEndpointLabel(asset);
  if (!endpoint) return null;
  const withoutQuery = endpoint.split("?", 1)[0] || endpoint;
  const parts = withoutQuery.split("/").filter(Boolean);
  return parts.at(-1) || withoutQuery.replace(/^(GET|POST|PUT|PATCH|DELETE)\s+/iu, "") || null;
}

function apiEndpointLabel(asset: AssetNamingInput) {
  if (asset.asset_type !== "api") return null;
  const identifiers = asset.identifiers ?? [];
  const identifier = identifiers.find((item) => stringRecordValue(item, "kind") === "api_endpoint") ?? identifiers[0];
  const value = stringRecordValue(identifier, "display_value") || stringRecordValue(identifier, "normalized_value") || asset.display_name;
  const match = value.match(/\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S+)/iu);
  if (match) return `${match[1].toUpperCase()} ${compactUrlPath(match[2])}`;
  return value || null;
}

function sqlQueryAlias(asset: AssetNamingInput, fallback: string) {
  const candidates = [asset.friendly_name, asset.display_name, fallback]
    .map((value) => value?.trim())
    .filter((value): value is string => Boolean(value));
  return candidates.find((value) => value.toLocaleLowerCase() !== "sql query") || "SQL query";
}

function pythonFunctionAlias(asset: AssetNamingInput, fallback: string) {
  const locator = functionLocator(asset);
  if (locator) return locator.split(".").filter(Boolean).at(-1) || locator;
  return asset.friendly_name || asset.display_name || fallback || asset.id;
}

function sqlQueryContext(query: string) {
  const tables = sqlTables(query);
  if (tables.length === 1) return `from ${tables[0]}`;
  if (tables.length > 1) return `${tables[0]} + ${tables.length - 1} table${tables.length === 2 ? "" : "s"}`;
  return compactSql(query);
}

function sqlTables(query: string | null | undefined) {
  if (!query) return [];
  const tables = Array.from(query.matchAll(/\b(?:from|join)\s+([`"[\]\w.-]+)/giu))
    .map((match) => cleanSqlIdentifier(match[1]))
    .filter((value): value is string => Boolean(value));
  return Array.from(new Set(tables));
}

function firstSqlTable(query: string | null | undefined) {
  if (!query) return null;
  const match = query.match(/\b(?:from|join)\s+([`"[\]\w.-]+)/iu);
  return cleanSqlIdentifier(match?.[1]);
}

function cleanSqlIdentifier(value: string | undefined) {
  return value?.replace(/^[`"[]|[`"\]]$/gu, "") || null;
}

function compactSql(query: string) {
  const table = firstSqlTable(query);
  if (table) return `from ${table}`;
  return normalizeWhitespace(query).slice(0, 80) || "sql query";
}

export function compactAssetPath(path: string) {
  const normalized = normalizeSlashes(path).replace(/\/+$/u, "");
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length >= 3) return parts.slice(-3).join("/");
  return normalized || path;
}

function pathContext(path: string, alias: string) {
  const normalized = normalizeSlashes(path).replace(/\/+$/u, "");
  const normalizedAlias = normalizeSlashes(alias).replace(/\/+$/u, "");
  if (!normalizedAlias) return compactAssetPath(normalized);
  if (normalized === normalizedAlias) return "";
  if (!normalized.endsWith(normalizedAlias)) return nonDuplicateText(compactAssetPath(normalized), alias);
  const context = normalized.slice(0, -normalizedAlias.length).replace(/\/+$/u, "");
  return context ? compactAssetPath(context) : "";
}

function apiEndpointContext(endpoint: string, alias: string) {
  const match = endpoint.match(/\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S+)/iu);
  if (!match) return nonDuplicateText(endpoint, alias);
  const method = match[1].toUpperCase();
  const endpointPath = match[2].split("?", 1)[0]?.replace(/\/+$/u, "") || "";
  const parts = endpointPath.split("/").filter(Boolean);
  if (!parts.length) return method;
  const parentPath = `/${parts.slice(0, -1).join("/")}`.replace(/\/$/u, "");
  return parentPath ? `${method} ${parentPath}` : method;
}

function functionContext(asset: AssetNamingInput, alias: string, showFull = false) {
  const functionName = functionLocator(asset);
  if (!functionName) return "python function";
  if (showFull) return nonDuplicateText(functionName, alias) || "python function";
  const parts = functionName.split(".").filter(Boolean);
  if (parts.length <= 1) return "python function";
  const parent = parts.slice(0, -1).join(".");
  return nonDuplicateText(parent, alias) || "python function";
}

function functionLocator(asset: AssetNamingInput) {
  const candidates = [
    asset.python_function,
    asset.display_name,
    asset.full_identity.split(" · ").at(-1),
  ].filter((value): value is string => Boolean(value?.trim()));
  return candidates.find((value) => value.split(".").filter(Boolean).length > 1) || candidates[0] || null;
}

function nonDuplicateText(value: string, alias: string) {
  const normalizedValue = value.trim();
  if (!normalizedValue) return "";
  const normalizedAlias = alias.trim();
  if (!normalizedAlias) return normalizedValue;
  return normalizedValue.toLocaleLowerCase() === normalizedAlias.toLocaleLowerCase() ? "" : normalizedValue;
}

function usesFriendlyAlias(asset: AssetNamingInput, title: string) {
  const friendlyName = asset.friendly_name?.trim();
  if (!friendlyName || friendlyName !== title.trim()) return false;
  const expectedLeaf = technicalLeaf(asset);
  return Boolean(expectedLeaf && friendlyName.toLocaleLowerCase() !== expectedLeaf.toLocaleLowerCase());
}

function technicalLeaf(asset: AssetNamingInput) {
  if (asset.asset_type === "python_function" || asset.python_function) {
    const locator = functionLocator(asset);
    return locator?.split(".").filter(Boolean).at(-1) || locator || null;
  }
  if (asset.asset_type === "sql_query" || asset.query) return "SQL query";
  if (asset.asset_type === "api") return apiEndpointAlias(asset);
  if (asset.asset_type === "table" && asset.table) return asset.table;
  if (asset.asset_type === "path" && asset.path) return compactAssetPath(asset.path);
  if (asset.table) return asset.table;
  if (asset.path) return compactAssetPath(asset.path);
  return null;
}

function normalizeWhitespace(value: string) {
  return value.replace(/\s+/gu, " ").trim();
}

function compactUrlPath(value: string) {
  try {
    const url = new URL(value);
    return `${url.pathname || "/"}${url.search || ""}`;
  } catch {
    return value;
  }
}

function normalizeSlashes(value: string) {
  return value.replace(/\\/g, "/");
}

function stringRecordValue(value: unknown, key: string) {
  if (!value || typeof value !== "object") return null;
  const field = (value as Record<string, unknown>)[key];
  return typeof field === "string" && field.trim() ? field.trim() : null;
}
