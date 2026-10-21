import { JSON_BLOCK_FIELDS, LIST_BLOCK_FIELDS, SQL_BLOCK_FIELDS, type DetailRow } from "./detailPrimitives";
import { StoryCoverage, StoryFields } from "./DataflowStoryValues";
import { StoryDisclosure } from "./DataflowStoryPrimitives";
import { storyIssues, type StoryPhase } from "./dataflowStoryModel";
import { parseRawRecordJson } from "../../../shared/api/monitoringAdapters";

const DATAFLOW_CONFIG_LABELS: Record<string, string> = {
  dataflow_id: "ID",
  dataflow_name: "Name",
  workspace_id: "Workspace ID",
  dataflow_description: "Description",
  stage: "Stage",
  operation_type: "Operation type",
  group_number: "Group number",
  execution_order: "Execution order",
  processing_mode: "Processing mode",
  datacoolie_version: "DataCoolie version",
  is_active: "Active",
  destination_operation_type: "Destination operation",
  configure: "Configure",
};

const CONFIG_RUNTIME_FIELDS = new Set([
  "source_start_time", "source_end_time", "source_duration_seconds", "source_status",
  "source_message", "source_error_message", "source_rows_read",
  "transform_start_time", "transform_end_time", "transform_duration_seconds",
  "transform_status", "transform_message", "transform_error_message", "transformers_applied",
  "destination_start_time", "destination_end_time", "destination_duration_seconds",
  "destination_status", "destination_message", "destination_error_message",
  "destination_rows_written", "destination_rows_inserted", "destination_rows_updated",
  "destination_rows_deleted", "destination_bytes_added", "destination_bytes_removed",
  "destination_bytes_saved", "destination_files_added", "destination_files_removed",
]);

export function DataflowStoryConfiguration({ row, diagnostics }: { row: Record<string, unknown>; timezoneName?: string | null; diagnostics?: unknown[] | null }) {
  const configDiagnostics = diagnostics?.filter(value => { const field = typeof value === "object" && value !== null ? (value as {field?: unknown}).field : null; return typeof field === "string" && (/^\/(source_|transform_|destination_|configure(?:\/|$))/u.test(field)); });
  return <section className="monitoring-detail-section dataflow-story-configuration"><StoryDisclosure title={<span>Configuration <StoryCoverage diagnostics={configDiagnostics} path="" /></span>}>{() => <><p className="monitor-muted dataflow-config-note">Metadata recorded for this run.</p><DataflowConfigurationBody row={row} diagnostics={diagnostics} /></>}</StoryDisclosure></section>;
}

export function DataflowConfigurationBody({ row, diagnostics }: { row: Record<string, unknown>; timezoneName?: string | null; diagnostics?: unknown[] | null }) {
  return <div className="dataflow-story-config-grid">
    <StoryConfigGroup title="Dataflow"
            rows={[
              ["ID", row.dataflow_id, "dataflow_id"],
              ["Name", row.dataflow_name, "dataflow_name"],
              ["Workspace ID", row.workspace_id, "workspace_id"],
              ["Description", row.dataflow_description, "dataflow_description"],
              ["Stage", row.stage, "stage"],
              ["Operation type", row.operation_type, "operation_type"],
              ["Group number", row.group_number, "group_number"],
              ["Execution order", row.execution_order, "execution_order"],
              ["Processing mode", row.processing_mode, "processing_mode"],
              ["DataCoolie version", row.datacoolie_version, "datacoolie_version"],
              ["Active", row.is_active, "is_active"],
              ["Destination operation", row.destination_operation_type, "destination_operation_type"],
              ["Configure", row.configure, "configure"],
            ]}
            row={row} diagnostics={diagnostics} />
    <div className="dataflow-story-config-phases">
    <StoryConfigGroup title="Source" phase="source"
            rows={[
              ["ID", row.source_id, "source_id"],
              ["Name", row.source_name, "source_name"],
              ["Connection type", row.source_connection_type, "source_connection_type"],
              ["Format",row.source_format,"source_format"],
              ["Catalog", row.source_catalog, "source_catalog"],
              ["Database", row.source_database, "source_database"],
              ["Schema", row.source_schema, "source_schema"],
              ["Table", row.source_table, "source_table"],
              ["Full table", row.source_full_table, "source_full_table"],
              ["Path", row.source_path, "source_path"],
              ["Query", row.source_query, "source_query"],
              ["Python function", row.source_python_function, "source_python_function"],
              ["Action", row.source_action, "source_action"],
              ["Watermark columns", row.source_watermark_columns, "source_watermark_columns"],
              ["Filter", row.source_filter_expression, "source_filter_expression"],
              ["Configure", row.source_configure, "source_configure"],
            ]}
            row={row} diagnostics={diagnostics} />
    <StoryConfigGroup title="Transform" phase="transform"
            rows={[
              ["Deduplicate", row.transform_deduplicate_columns, "transform_deduplicate_columns"],
              ["Latest data", row.transform_latest_data_columns, "transform_latest_data_columns"],
              ["Filter", row.transform_filter_expression, "transform_filter_expression"],
              ["Additional columns", row.transform_additional_columns, "transform_additional_columns"],
              ["Schema hints", row.transform_schema_hints, "transform_schema_hints"],
              ["Select columns", row.transform_select_columns, "transform_select_columns"],
              ["Drop columns", row.transform_drop_columns, "transform_drop_columns"],
              ["Rename columns", row.transform_rename_columns, "transform_rename_columns"],
              ["Value rules", row.transform_value_rules, "transform_value_rules"],
              ["Hash columns", row.transform_hash_columns, "transform_hash_columns"],
              ["Masking rules", row.transform_masking_rules, "transform_masking_rules"],
              ["Configure", row.transform_configure, "transform_configure"],
            ]}
            row={row} diagnostics={diagnostics} />
    <StoryConfigGroup title="Destination" phase="destination"
            rows={[
              ["ID", row.destination_id, "destination_id"],
              ["Name", row.destination_name, "destination_name"],
              ["Connection type", row.destination_connection_type, "destination_connection_type"],
              ["Format",row.destination_format,"destination_format"],
              ["Catalog", row.destination_catalog, "destination_catalog"],
              ["Database", row.destination_database, "destination_database"],
              ["Schema", row.destination_schema, "destination_schema"],
              ["Table", row.destination_table, "destination_table"],
              ["Full table", row.destination_full_table, "destination_full_table"],
              ["Path", row.destination_path, "destination_path"],
              ["Load type", row.destination_load_type, "destination_load_type"],
              ["Merge keys", row.destination_merge_keys, "destination_merge_keys"],
              ["Partition columns", row.destination_partition_columns, "destination_partition_columns"],
              ["Configure", row.destination_configure, "destination_configure"],
            ]}
            row={row} diagnostics={diagnostics} />
    </div>
  </div>;
}

function StoryConfigGroup({ title, phase, rows, row, diagnostics }: { title: string; phase?: StoryPhase; rows: DetailRow[]; row: Record<string, unknown>; diagnostics?: unknown[] | null }) {
  const known = new Set(rows.flatMap(([, , field]) => field ? [field] : []));
  const extra = Object.keys(row)
    .filter(field => !known.has(field) && isGroupField(field, phase))
    .sort((left, right) => left.localeCompare(right))
    .map((field): DetailRow => [fieldLabel(field), row[field], field]);
  const visible = [...rows, ...extra].filter(([, value, field]) =>
    Boolean(field && Object.prototype.hasOwnProperty.call(row, field) && value !== undefined)
      || Boolean(field && storyIssues(diagnostics, `/${field}`).length),
  );
  if (!visible.length) return null;
  return <section className="dataflow-story-config-group" data-config-section={phase ?? "dataflow"}><h4>{title}</h4><StoryFields expandStructured diagnostics={diagnostics} fields={visible.map(([label,value,field]) => ({label,value,path:`/${field}`,text:true,kind:configurationValueKind(field ?? "", value)}))} /></section>;
}

function isGroupField(field: string, phase?: StoryPhase) {
  if (!phase) return Boolean(DATAFLOW_CONFIG_LABELS[field]);
  const prefix = `${phase}_`;
  return field.startsWith(prefix) && !CONFIG_RUNTIME_FIELDS.has(field);
}

function configurationValueKind(field: string, value: unknown): "sql" | "json" | undefined {
  if (SQL_BLOCK_FIELDS.has(field)) return "sql";
  if (JSON_BLOCK_FIELDS.has(field) || LIST_BLOCK_FIELDS.has(field)) return "json";
  if (value !== null && typeof value === "object") return "json";
  if (typeof value === "string") {
    const parsed = parseRawRecordJson(value);
    if (parsed !== null && typeof parsed === "object") return "json";
  }
  return undefined;
}

function fieldLabel(field: string) {
  return DATAFLOW_CONFIG_LABELS[field]
    ?? field.replace(/_/gu, " ").replace(/\b\w/gu, letter => letter.toUpperCase());
}
