import type { Dataflow, Endpoint, MetadataEditorDocument, MetadataEditorIssue, MetadataResponse } from "../../shared/api/domainTypes";
import { formatCellValue, parseStructuredValue } from "../metadata-explorer/metadataSheetOperations";

export interface MetadataDataflowSelection {
  metadataSourceId?: number | null;
  rowIndex?: number | null;
  dataflowId?: string | null;
  name?: string | null;
}

export interface MetadataDataflowRecord {
  key: string;
  rowIndex: number;
  editorBacked: boolean;
  row: Record<string, unknown>;
  columnKeys: string[];
  normalized?: Dataflow;
  issues: MetadataEditorIssue[];
  metadataSourceId: number | null;
  metadataSourceName: string;
  metadataSourceUri: string;
  dataflowId: string;
  name: string;
  stage: string;
  description: string;
  processingMode: string;
  isActive: boolean;
  loadType: string;
  source: MetadataDataflowEndpoint;
  destination: MetadataDataflowEndpoint;
  transformRows: MetadataDataflowField[];
  customRows: MetadataDataflowField[];
}

export interface MetadataDataflowEndpoint {
  connectionName: string;
  schemaName: string;
  table: string;
  path: string;
  query: string;
  pythonFunction: string;
  configure: unknown;
  assetId: string;
}

export interface MetadataDataflowField {
  key: string;
  label: string;
  value: unknown;
  structured: boolean;
}

const DATAFLOW_KEYS = [
  "dataflow_id",
  "workspace_id",
  "name",
  "description",
  "stage",
  "group_number",
  "execution_order",
  "processing_mode",
  "is_active",
  "configure",
];

const PRIMARY_KEYS = new Set(DATAFLOW_KEYS);

const SOURCE_KEYS = [
  "source_connection_name",
  "source_schema_name",
  "source_table",
  "source_query",
  "source_python_function",
  "source_watermark_columns",
  "source_filter_expression",
  "source_configure",
];

const TRANSFORM_KEYS = [
  "transform_filter_expression",
  "transform_deduplicate_columns",
  "transform_latest_data_columns",
  "transform_additional_columns",
  "transform_schema_hints",
  "transform_configure",
];

const DESTINATION_KEYS = [
  "destination_connection_name",
  "destination_schema_name",
  "destination_table",
  "destination_load_type",
  "destination_merge_keys",
  "destination_partition_columns",
  "destination_configure",
];

const KNOWN_KEYS = new Set([
  ...PRIMARY_KEYS,
  ...SOURCE_KEYS,
  ...TRANSFORM_KEYS,
  ...DESTINATION_KEYS,
  "__metadata_source_id",
  "__metadata_source_name",
  "__metadata_source_uri",
  "__metadata_source_kind",
]);

export function buildMetadataDataflowRecords(
  document: MetadataEditorDocument | null,
  metadata: MetadataResponse | null = null,
): MetadataDataflowRecord[] {
  const rows = document?.sheets.dataflows?.rows ?? [];
  const columnKeys = document?.sheets.dataflows?.columns.map((column) => column.key) ?? [];
  const issues = document?.issues ?? [];
  const normalizedByKey = normalizedDataflowLookup(metadata);
  const records: MetadataDataflowRecord[] = rows.map((row, rowIndex) => {
    const metadataSourceId = numberValue(row.__metadata_source_id);
    const name = textValue(row.name) || `Dataflow ${rowIndex + 1}`;
    const dataflowId = textValue(row.dataflow_id);
    const normalized = findNormalizedDataflow(normalizedByKey, metadataSourceId, dataflowId, name);
    const source = endpointFromRow(row, "source", normalized?.source, normalized?.source_asset_id);
    const destination = endpointFromRow(row, "destination", normalized?.destination, normalized?.destination_asset_id);
    const loadType = textValue(row.destination_load_type) || textValue(normalized?.load_type);
    return {
      key: `${metadataSourceId ?? "source"}:${rowIndex}`,
      rowIndex,
      editorBacked: true,
      row,
      columnKeys,
      normalized,
      issues: issues.filter((issue) => issue.sheet === "dataflows" && issue.row_index === rowIndex),
      metadataSourceId,
      metadataSourceName: textValue(row.__metadata_source_name) || document?.source.name || "metadata source",
      metadataSourceUri: textValue(row.__metadata_source_uri) || document?.source.uri || "",
      dataflowId: dataflowId || textValue(normalized?.dataflow_id),
      name,
      stage: textValue(row.stage) || textValue(normalized?.stage),
      description: textValue(row.description) || textValue(normalized?.description),
      processingMode: textValue(row.processing_mode) || textValue(normalized?.processing_mode) || "batch",
      isActive: row.is_active !== false,
      loadType,
      source,
      destination,
      transformRows: fieldsFromKeys(row, TRANSFORM_KEYS, columnKeys),
      customRows: customFields(row, columnKeys),
    };
  });
  const represented = new Set(records.flatMap(recordDataflowKeys));
  let syntheticIndex = 0;
  for (const dataflow of metadata?.dataflows ?? []) {
    const keys = normalizedDataflowKeys(dataflow);
    if (keys.some((key) => represented.has(key))) continue;
    const record = recordFromNormalizedDataflow(dataflow, rows.length + syntheticIndex);
    syntheticIndex += 1;
    records.push(record);
    for (const key of keys) represented.add(key);
  }
  return records;
}

export function findMetadataDataflowRecord(
  records: MetadataDataflowRecord[],
  selection: MetadataDataflowSelection | null,
) {
  if (!selection) return null;
  const sourceId = normalizeSourceId(selection.metadataSourceId);
  const rowIndex = normalizeRowIndex(selection.rowIndex);
  if (rowIndex !== null) {
    const exact = records.find((record) =>
      record.rowIndex === rowIndex
      && (sourceId === null || record.metadataSourceId === sourceId)
    );
    if (exact) return exact;
  }

  const dataflowId = normalizeText(selection.dataflowId);
  if (dataflowId) {
    const matches = records.filter((record) =>
      normalizeText(record.dataflowId) === dataflowId
      && (sourceId === null || record.metadataSourceId === sourceId)
    );
    if (matches.length === 1) return matches[0];
  }

  const name = normalizeText(selection.name);
  if (name) {
    const matches = records.filter((record) =>
      normalizeText(record.name) === name
      && (sourceId === null || record.metadataSourceId === sourceId)
    );
    if (matches.length === 1) return matches[0];
  }
  return null;
}

export function updateMetadataDataflowRow(
  document: MetadataEditorDocument,
  rowIndex: number,
  nextRow: Record<string, unknown>,
): MetadataEditorDocument {
  const dataflows = document.sheets.dataflows;
  if (!dataflows || rowIndex < 0 || rowIndex >= dataflows.rows.length) return document;
  const currentRow = dataflows.rows[rowIndex];
  const safeNextRow = {
    ...nextRow,
    dataflow_id: currentRow.dataflow_id,
  };
  return {
    ...document,
    sheets: {
      ...document.sheets,
      dataflows: {
        ...dataflows,
        rows: dataflows.rows.map((row, index) => (index === rowIndex ? safeNextRow : row)),
      },
    },
  };
}

export function isEditableMetadataDataflowRecord(
  document: MetadataEditorDocument | null,
  record: MetadataDataflowRecord | null,
) {
  if (!document || !record?.editorBacked || document.source.read_only) return false;
  const rows = document.sheets.dataflows?.rows ?? [];
  return record.rowIndex >= 0 && record.rowIndex < rows.length;
}

export function dataflowRouteText(record: MetadataDataflowRecord) {
  const source = routeEndpointLabel(routeEndpointParts(record, "source"));
  const destination = routeEndpointLabel(routeEndpointParts(record, "destination"));
  const route = `${source} → ${destination}`;
  const loadType = dataflowRouteLoadType(record);
  return loadType ? `${route} : ${loadType}` : route;
}

export function dataflowRouteLoadType(record: MetadataDataflowRecord) {
  return textValue(record.row.destination_load_type) || record.loadType;
}

export function dataflowTitle(record: MetadataDataflowRecord) {
  return record.name || record.dataflowId || `Dataflow ${record.rowIndex + 1}`;
}

export function endpointLabel(endpoint: MetadataDataflowEndpoint) {
  return routeEndpointLabel(endpointRouteParts(endpoint));
}

function routeEndpointLabel({ connectionName, locator, kind }: ReturnType<typeof endpointRouteParts>) {
  if (kind === "sql_query") return connectionName ? `${connectionName} - SQL query` : "SQL query";
  if (!connectionName) return locator || "-";
  return locator ? `${connectionName} - ${locator}` : connectionName;
}

export function endpointRouteParts(endpoint: MetadataDataflowEndpoint) {
  const tableLocator = [endpoint.schemaName, endpoint.table].filter(Boolean).join(".");
  const locator = tableLocator
    || endpoint.query
    || endpoint.pythonFunction
    || endpoint.path
    || endpoint.assetId;
  return {
    connectionName: endpoint.connectionName,
    locator,
    kind: !tableLocator && endpoint.query ? "sql_query" : "endpoint",
  };
}

export function routeEndpointParts(record: MetadataDataflowRecord, prefix: "source" | "destination") {
  const endpoint = prefix === "source" ? record.source : record.destination;
  const raw = (key: string) => textValue(record.row[key]);
  const connectionName = raw(`${prefix}_connection_name`) || endpoint.connectionName;
  const tableLocator = [raw(`${prefix}_schema_name`) || raw(`${prefix}_schema`), raw(`${prefix}_table`)]
    .filter(Boolean)
    .join(".");
  const query = raw(`${prefix}_query`) || endpoint.query;
  const locator = tableLocator
    || query
    || raw(`${prefix}_python_function`)
    || raw(`${prefix}_path`)
    || endpointRouteParts(endpoint).locator;
  return { connectionName, locator, kind: !tableLocator && query ? "sql_query" : "endpoint" };
}

export function dataflowFields(record: MetadataDataflowRecord) {
  const fields = fieldsFromKeys(record.row, DATAFLOW_KEYS, record.columnKeys);
  for (const field of fields) field.value = dataflowFieldValue(record, field.key);
  const workspaceIndex = fields.findIndex((field) => field.key === "workspace_id");
  const dataflowIdIndex = fields.findIndex((field) => field.key === "dataflow_id");
  if (workspaceIndex >= 0 && dataflowIdIndex >= 0 && workspaceIndex !== dataflowIdIndex + 1) {
    const [workspace] = fields.splice(workspaceIndex, 1);
    fields.splice(fields.findIndex((field) => field.key === "dataflow_id") + 1, 0, workspace);
  }
  return [...fields, ...record.customRows];
}

export function sourceFields(record: MetadataDataflowRecord) {
  return fieldsFromKeys(record.row, SOURCE_KEYS, record.columnKeys);
}

export function destinationFields(record: MetadataDataflowRecord) {
  return fieldsFromKeys(record.row, DESTINATION_KEYS, record.columnKeys);
}

export function isStructuredDataflowField(key: string, value: unknown) {
  return key === "configure"
    || key.endsWith("_configure")
    || key === "source_query"
    || key === "source_filter_expression"
    || key === "transform_filter_expression"
    || key === "transform_additional_columns"
    || key === "transform_schema_hints"
    || key === "destination_partition_columns"
    || Boolean(parseStructuredValue(value));
}

export function displayDataflowValue(value: unknown) {
  return formatCellValue(value) || "-";
}

export function labelFromKey(key: string) {
  return DATAFLOW_FIELD_LABELS[key] ?? key.replace(/_/g, " ");
}

const DATAFLOW_FIELD_LABELS: Record<string, string> = {
  dataflow_id: "Dataflow ID",
  name: "Name",
  description: "Description",
  stage: "Stage",
  workspace_id: "Workspace ID",
  configure: "Configure",
  processing_mode: "Processing mode",
  is_active: "Active",
  group_number: "Group number",
  execution_order: "Execution order",
  source_id: "Source ID",
  source_name: "Source name",
  source_connection_name: "Connection name",
  source_connection_type: "Connection type",
  source_format: "Format",
  source_catalog: "Catalog",
  source_database: "Database",
  source_schema: "Schema",
  source_schema_name: "Schema",
  source_table: "Table",
  source_full_table: "Full table",
  source_path: "Path",
  source_query: "SQL query",
  source_python_function: "Python function",
  source_watermark_columns: "Watermark columns",
  source_filter_expression: "Filter",
  source_configure: "Configure",
  transform_filter_expression: "Filter",
  transform_deduplicate_columns: "Deduplicate",
  transform_latest_data_columns: "Latest data",
  transform_additional_columns: "Additional columns",
  transform_schema_hints: "Schema hints",
  transform_configure: "Configure",
  destination_id: "Destination ID",
  destination_name: "Destination name",
  destination_connection_name: "Connection name",
  destination_connection_type: "Connection type",
  destination_format: "Format",
  destination_catalog: "Catalog",
  destination_database: "Database",
  destination_schema: "Schema",
  destination_schema_name: "Schema",
  destination_table: "Table",
  destination_full_table: "Full table",
  destination_path: "Path",
  destination_load_type: "Load type",
  destination_merge_keys: "Merge keys",
  destination_partition_columns: "Partition columns",
  destination_configure: "Configure",
};

function dataflowFieldValue(record: MetadataDataflowRecord, key: string) {
  const rowValue = record.row[key];
  if (!isEmptyValue(rowValue)) return rowValue;
  switch (key) {
    case "name":
      return record.name;
    case "dataflow_id":
      return record.dataflowId;
    case "description":
      return record.description;
    case "stage":
      return record.stage;
    case "processing_mode":
      return record.processingMode;
    case "is_active":
      return record.isActive;
    default:
      return rowValue;
  }
}

function endpointFromRow(
  row: Record<string, unknown>,
  prefix: "source" | "destination",
  normalized: Endpoint | undefined,
  assetId: string | undefined,
): MetadataDataflowEndpoint {
  return {
    connectionName: textValue(row[`${prefix}_connection_name`]) || textValue(row[`${prefix}_name`]) || textValue(normalized?.connection_name),
    schemaName: textValue(row[`${prefix}_schema_name`]) || textValue(row[`${prefix}_schema`]) || textValue(normalized?.schema_name),
    table: textValue(row[`${prefix}_table`]) || textValue(normalized?.table),
    path: textValue(row[`${prefix}_path`]) || textValue(normalized?.path),
    query: textValue(row[`${prefix}_query`]) || textValue(normalized?.query),
    pythonFunction: textValue(row[`${prefix}_python_function`]) || textValue(normalized?.python_function),
    configure: row[`${prefix}_configure`] ?? null,
    assetId: assetId || "",
  };
}

function fieldsFromKeys(row: Record<string, unknown>, keys: string[], columnKeys: string[] = []): MetadataDataflowField[] {
  const sheetOrder = columnKeys.filter((key) => keys.includes(key));
  const orderedKeys = sheetOrder.length
    ? [...sheetOrder, ...keys.filter((key) => !sheetOrder.includes(key))]
    : keys;
  return orderedKeys.map((key) => ({
      key,
      label: labelFromKey(key),
      value: row[key],
      structured: isStructuredDataflowField(key, row[key]),
    }));
}

function customFields(row: Record<string, unknown>, columnKeys: string[]): MetadataDataflowField[] {
  const keys = columnKeys.length
    ? columnKeys.filter((key) => !KNOWN_KEYS.has(key) && !key.startsWith("__"))
    : Object.keys(row).filter((key) => !KNOWN_KEYS.has(key) && !key.startsWith("__"));
  return keys.map((key) => {
    const value = row[key];
    return {
      key,
      label: labelFromKey(key),
      value,
      structured: isStructuredDataflowField(key, value),
    };
  });
}

function normalizedDataflowLookup(metadata: MetadataResponse | null) {
  const lookup = new Map<string, Dataflow>();
  for (const dataflow of metadata?.dataflows ?? []) {
    const sourceId = normalizeSourceId(dataflow.metadata_source_id);
    if (sourceId === null) continue;
    const dataflowId = normalizeText(dataflow.dataflow_id);
    const name = normalizeText(dataflow.name);
    if (dataflowId) lookup.set(`${sourceId}:id:${dataflowId}`, dataflow);
    if (name) lookup.set(`${sourceId}:name:${name}`, dataflow);
  }
  return lookup;
}

function recordFromNormalizedDataflow(dataflow: Dataflow, rowIndex: number): MetadataDataflowRecord {
  const row = rowFromNormalizedDataflow(dataflow);
  return {
    key: `${normalizeSourceId(dataflow.metadata_source_id) ?? "source"}:normalized:${normalizeText(dataflow.dataflow_id) || rowIndex}`,
    rowIndex,
    editorBacked: false,
    row,
    columnKeys: [],
    normalized: dataflow,
    issues: [],
    metadataSourceId: normalizeSourceId(dataflow.metadata_source_id),
    metadataSourceName: textValue(dataflow.metadata_source_uri) || "metadata source",
    metadataSourceUri: textValue(dataflow.metadata_source_uri),
    dataflowId: textValue(dataflow.dataflow_id),
    name: textValue(dataflow.name) || textValue(dataflow.dataflow_id) || `Dataflow ${rowIndex + 1}`,
    stage: textValue(dataflow.stage),
    description: textValue(dataflow.description),
    processingMode: textValue(dataflow.processing_mode) || "batch",
    isActive: dataflow.is_active !== false,
    loadType: textValue(dataflow.load_type),
    source: endpointFromRow(row, "source", dataflow.source, dataflow.source_asset_id),
    destination: endpointFromRow(row, "destination", dataflow.destination, dataflow.destination_asset_id),
    transformRows: [],
    customRows: [],
  };
}

function rowFromNormalizedDataflow(dataflow: Dataflow): Record<string, unknown> {
  return {
    __metadata_source_id: dataflow.metadata_source_id,
    __metadata_source_uri: dataflow.metadata_source_uri,
    dataflow_id: dataflow.dataflow_id,
    name: dataflow.name,
    description: dataflow.description,
    stage: dataflow.stage,
    processing_mode: dataflow.processing_mode,
    source_connection_name: dataflow.source?.connection_name,
    source_schema_name: dataflow.source?.schema_name,
    source_table: dataflow.source?.table,
    source_path: dataflow.source?.path,
    source_query: dataflow.source?.query,
    source_python_function: dataflow.source?.python_function,
    destination_connection_name: dataflow.destination?.connection_name,
    destination_schema_name: dataflow.destination?.schema_name,
    destination_table: dataflow.destination?.table,
    destination_path: dataflow.destination?.path,
    destination_load_type: dataflow.load_type,
  };
}

function recordDataflowKeys(record: MetadataDataflowRecord) {
  const sourceId = normalizeSourceId(record.metadataSourceId);
  if (sourceId === null) return [];
  return [
    record.dataflowId ? `${sourceId}:id:${normalizeText(record.dataflowId)}` : "",
    record.name ? `${sourceId}:name:${normalizeText(record.name)}` : "",
    record.normalized?.dataflow_id ? `${sourceId}:id:${normalizeText(record.normalized.dataflow_id)}` : "",
    record.normalized?.name ? `${sourceId}:name:${normalizeText(record.normalized.name)}` : "",
  ].filter(Boolean);
}

function normalizedDataflowKeys(dataflow: Dataflow) {
  const sourceId = normalizeSourceId(dataflow.metadata_source_id);
  if (sourceId === null) return [];
  return [
    dataflow.dataflow_id ? `${sourceId}:id:${normalizeText(dataflow.dataflow_id)}` : "",
    dataflow.name ? `${sourceId}:name:${normalizeText(dataflow.name)}` : "",
  ].filter(Boolean);
}

function findNormalizedDataflow(
  lookup: Map<string, Dataflow>,
  sourceId: number | null,
  dataflowId: string,
  name: string,
) {
  if (sourceId === null) return undefined;
  const normalizedId = normalizeText(dataflowId);
  const normalizedName = normalizeText(name);
  return (normalizedId ? lookup.get(`${sourceId}:id:${normalizedId}`) : undefined)
    ?? (normalizedName ? lookup.get(`${sourceId}:name:${normalizedName}`) : undefined);
}

function normalizeSourceId(value: unknown) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
}

function normalizeRowIndex(value: unknown) {
  const numeric = Number(value);
  return Number.isInteger(numeric) && numeric >= 0 ? numeric : null;
}

function numberValue(value: unknown) {
  return normalizeSourceId(value);
}

function textValue(value: unknown) {
  if (value == null) return "";
  return String(value).trim();
}

function normalizeText(value: unknown) {
  return textValue(value).toLocaleLowerCase();
}

function isEmptyValue(value: unknown) {
  return value == null || value === "";
}
