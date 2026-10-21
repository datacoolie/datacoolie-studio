import type {
  MetadataBackupResponseTransport,
  MetadataEditorDocumentResponseTransport,
  MetadataEditorWorkspaceResponseTransport,
  MetadataWriteOperationResponseTransport,
  MetadataResponseTransport,
} from "./contractTypes";
import {
  asRecord,
  nullableNumber,
  nullableRecord,
  nullableString,
  numberValue,
  optionalBoolean,
  optionalRecord,
  optionalString,
  recordArray,
  recordMap,
  stringValue,
} from "./transportValues";
import type {
  Dataflow,
  Endpoint,
  MetadataBackup,
  MetadataEditorColumn,
  MetadataEditorDocument,
  MetadataEditorIssue,
  MetadataEditorSheet,
  MetadataEditorWorkspace,
  MetadataResponse,
  MetadataWriteItem,
  MetadataWriteOperation,
  PendingSqlFileOperation,
  WriteSafety,
} from "./domainTypes";

const WRITE_SAFETY_VALUES = [
  "strong_cas",
  "cooperative",
  "verified",
  "read_only",
] as const satisfies readonly WriteSafety[];

export function toMetadataWriteOperation(value: MetadataWriteOperationResponseTransport): MetadataWriteOperation {
  const response = asRecord(value);
  return {
    operation_key: stringValue(response.operation_key),
    environment_id: numberValue(response.environment_id),
    state: stringValue(response.state),
    created_at: stringValue(response.created_at),
    updated_at: stringValue(response.updated_at),
    completed_at: response.completed_at == null ? null : stringValue(response.completed_at),
    expires_at: stringValue(response.expires_at),
    error: response.error == null ? null : asRecord(response.error),
    summary: Object.fromEntries(
      Object.entries(asRecord(response.summary)).map(([key, item]) => [key, numberValue(item)]),
    ),
    items: recordArray(response.items).map(toMetadataWriteItem),
  };
}

function toMetadataWriteItem(value: unknown): MetadataWriteItem {
  const item = asRecord(value);
  return {
    id: numberValue(item.id),
    order: numberValue(item.order),
    kind: stringValue(item.kind),
    state: stringValue(item.state),
    source_id: nullableNumber(item.source_id),
    source_name: nullableString(item.source_name),
    source_uri: stringValue(item.source_uri),
    target_uri: stringValue(item.target_uri),
    action: stringValue(item.action),
    label: stringValue(item.label),
    sheets: Array.isArray(item.sheets)
      ? item.sheets.filter((sheet): sheet is string => typeof sheet === "string")
      : [],
    provider: nullableString(item.provider),
    write_safety: nullableWriteSafety(item.write_safety),
    error: item.error == null ? null : asRecord(item.error),
  };
}

function nullableWriteSafety(value: unknown): WriteSafety | null {
  const candidate = nullableString(value);
  return candidate !== null && WRITE_SAFETY_VALUES.includes(candidate as WriteSafety)
    ? candidate as WriteSafety
    : null;
}

export function toMetadataResponse(
  response: MetadataResponseTransport,
): MetadataResponse {
  return {
    summary: asRecord(response.summary),
    sources: recordArray(response.sources),
    connections: recordArray(response.connections),
    dataflows: recordArray(response.dataflows).map(toDataflow),
    schema_hints: recordArray(response.schema_hints),
    errors: recordArray(response.errors),
  };
}

export function toMetadataEditorWorkspace(
  response: MetadataEditorWorkspaceResponseTransport,
): MetadataEditorWorkspace {
  return {
    schema_version: response.schema_version,
    environment_id: response.environment_id,
    metadata_catalog_version: response.metadata_catalog_version,
    document: toMetadataEditorDocument(response.document),
    draft: response.draft === undefined || response.draft === null
      ? null
      : toMetadataEditorDocument(response.draft),
  };
}

export function toMetadataEditorDocument(
  response: MetadataEditorDocumentResponseTransport,
): MetadataEditorDocument {
  return toEditorDocument(response);
}

export function toMetadataBackup(
  response: MetadataBackupResponseTransport,
): MetadataBackup {
  return {
    id: response.id,
    project_id: response.project_id,
    environment_id: response.environment_id,
    source_id: response.source_id,
    source_uri: response.source_uri,
    backup_path: response.backup_path ?? null,
    backup_ref: response.backup_ref ?? null,
    backup_hash: response.backup_hash ?? null,
    backup_size: response.backup_size ?? null,
    source_revision: nullableRecord(response.source_revision),
    saved_revision: nullableRecord(response.saved_revision),
    created_at: response.created_at,
  };
}

export function toMetadataBackups(
  responses: MetadataBackupResponseTransport[],
): MetadataBackup[] {
  return responses.map(toMetadataBackup);
}

function toDataflow(value: unknown): Dataflow {
  const dataflow = asRecord(value);
  return {
    metadata_source_id: numberValue(dataflow.metadata_source_id),
    metadata_source_uri: stringValue(dataflow.metadata_source_uri),
    dataflow_id: nullableString(dataflow.dataflow_id),
    name: stringValue(dataflow.name),
    description: nullableString(dataflow.description),
    stage: nullableString(dataflow.stage),
    processing_mode: nullableString(dataflow.processing_mode),
    is_active: optionalBoolean(dataflow.is_active),
    load_type: nullableString(dataflow.load_type),
    source: toEndpoint(dataflow.source),
    destination: toEndpoint(dataflow.destination),
    source_asset_id: optionalString(dataflow.source_asset_id),
    destination_asset_id: optionalString(dataflow.destination_asset_id),
  };
}

function toEndpoint(value: unknown): Endpoint | undefined {
  if (value === undefined || value === null) return undefined;
  const endpoint = asRecord(value);
  return {
    connection_name: nullableString(endpoint.connection_name),
    connection_type: nullableString(endpoint.connection_type),
    format: nullableString(endpoint.format),
    catalog: nullableString(endpoint.catalog),
    database: nullableString(endpoint.database),
    schema_name: nullableString(endpoint.schema_name),
    table: nullableString(endpoint.table),
    path: nullableString(endpoint.path),
    query: nullableString(endpoint.query),
    python_function: nullableString(endpoint.python_function),
    load_type: nullableString(endpoint.load_type),
  };
}

function toEditorDocument(value: unknown): MetadataEditorDocument {
  const document = asRecord(value);
  return {
    source: toEditorSource(document.source),
    sheets: Object.fromEntries(
      Object.entries(asRecord(document.sheets)).map(([key, sheet]) => [key, toEditorSheet(sheet)]),
    ),
    issues: recordArray(document.issues).map(toMetadataEditorIssue),
    metadata_document: optionalRecord(document.metadata_document),
    metadata_documents: recordMap(document.metadata_documents),
    sql_files: recordArray(document.sql_files).map(toPendingSqlFileOperation),
  };
}

function toEditorSource(value: unknown): MetadataEditorDocument["source"] {
  const source = asRecord(value);
  return {
    source_id: numberValue(source.source_id),
    environment_id: numberValue(source.environment_id),
    project_id: nullableNumber(source.project_id),
    uri: stringValue(source.uri),
    name: optionalString(source.name),
    format: stringValue(source.format, "json"),
    scope: optionalString(source.scope),
    read_only: optionalBoolean(source.read_only),
    revision: asRecord(source.revision),
  };
}

function toEditorSheet(value: unknown): MetadataEditorSheet {
  const sheet = asRecord(value);
  return {
    columns: recordArray(sheet.columns).map(toEditorColumn),
    rows: recordArray(sheet.rows),
  };
}

function toEditorColumn(value: unknown): MetadataEditorColumn {
  const column = asRecord(value);
  const key = stringValue(column.key, stringValue(column.name));
  return {
    key,
    name: stringValue(column.name, key),
  };
}

function toMetadataEditorIssue(value: unknown): MetadataEditorIssue {
  const issue = asRecord(value);
  return {
    severity: stringValue(issue.severity, "error"),
    sheet: stringValue(issue.sheet),
    row_index: numberValue(issue.row_index),
    column: stringValue(issue.column),
    message: stringValue(issue.message),
  };
}

function toPendingSqlFileOperation(value: unknown): PendingSqlFileOperation {
  const operation = asRecord(value);
  return {
    source_id: numberValue(operation.source_id),
    query: stringValue(operation.query),
    content: stringValue(operation.content),
    base_revision: nullableRecord(operation.base_revision),
    base_kind: optionalString(operation.base_kind),
    relative_path: optionalString(operation.relative_path),
    base_uri: nullableString(operation.base_uri),
    config_revision: nullableRecord(operation.config_revision),
    publish_status: toPublishStatus(operation.publish_status),
    published_revision: nullableRecord(operation.published_revision),
    published_content_hash: nullableString(operation.published_content_hash),
  };
}

function toPublishStatus(value: unknown): PendingSqlFileOperation["publish_status"] {
  return value === "pending" || value === "applied" ? value : undefined;
}
