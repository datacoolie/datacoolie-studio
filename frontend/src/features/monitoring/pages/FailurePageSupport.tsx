import { Help } from "../../../shared/components/Help";
import { monitoringDimensionLabel } from "../monitoringDimensions";
import {
  DataTable,
} from "../../../shared/components/DataTable";
import { formatTimestampForDisplay } from "../../../shared/time";
import { LineageFormatIcon } from "../../../shared/components/FormatIcon";
import { Tag } from "../../../shared/components/Tag";
import { assetIconKind } from "../../../shared/formatPresentation";
import { FAILURE_PHASE_COLORS, FAILURE_PHASE_LABELS, FAILURE_TREND_COLORS } from "./FailurePagePresentation";
import { FAILURE_PHASES } from "../components/monitoringModel";
import { CompactNumberValue, CompactValue, IssuePreview, MonitoringChartLegend, TableDateTimeValue, humanize } from "../components/monitoringPresentation";
import { monitoringConnectionStageClass } from "../monitoringStagePresentation";

type FailureActivityRow = Record<string, string | number | null>;

function isRecoveredAttempt(row: FailureActivityRow) {
  return row.activity_type === "recovered_attempt";
}

function failureActivityTime(row: FailureActivityRow) {
  return row.activity_time ?? row.failure_time ?? row.event_time;
}

function failureActivityMessage(row: FailureActivityRow) {
  return row.activity_message ?? row.failure_message ?? row.attempt_message;
}

function failureActivityTimeTitle(row: FailureActivityRow) {
  if (row.activity_time_source === "attempt_end_time") return "Failed attempt end time";
  if (row.activity_time_source === "attempt_start_time") return "Failed attempt start time";
  if (isRecoveredAttempt(row)) return "Dataflow run time; attempt time was not recorded";
  return "Dataflow failure time";
}

export function FailureQueueTable({
  rows,
  maxRows,
  offset = 0,
  onInspect,
  timezoneName
}: {
  rows: Array<Record<string, string | number | null>>;
  maxRows: number;
  offset?: number;
  onInspect?: (row: Record<string, unknown>) => void;
  timezoneName?: string | null;
}) {
  return (
    <DataTable<Record<string, string | number | null>>
      rows={rows}
      compactNumbers
      compactNumberStyle="infix"
      columns={[
        {
          key: "activity_time",
          label: "Time",
          sortable: true,
          autoFit: true,
          maxWidth: 190,
          render: (row) => <Help as="span" title={failureActivityTimeTitle(row)}><TableDateTimeValue value={failureActivityTime(row)} timezoneName={timezoneName} /></Help>,
          measureValue: (row, activeTimezone) => formatTimestampForDisplay(failureActivityTime(row), activeTimezone, "-")
        },
        {
          key: "activity_type",
          label: "Type",
          sortable: true,
          autoFit: true,
          maxWidth: 145,
          render: (row) => isRecoveredAttempt(row)
            ? <Help as="span" title="Failed attempt within a dataflow run that later succeeded"><Tag tone="warning">Recovered</Tag></Help>
            : <Help as="span" title="Terminally failed dataflow run"><Tag tone="danger">Failed</Tag></Help>,
          measureValue: (row) => isRecoveredAttempt(row) ? "Recovered" : "Failed"
        },
        { key: "dataflow_name", label: "Dataflow", sortable: true, width: 178, render: (row) => <FailureDataflowNameCell row={row} /> },
        {
          key: "activity_context",
          label: "Context",
          sortable: true,
          autoFit: true,
          maxWidth: 136,
          render: (row) => isRecoveredAttempt(row)
            ? <span className="failure-phase-badge is-attempt">Attempt {row.attempt_number == null ? "unk" : String(row.attempt_number)}</span>
            : <FailurePhaseBadge value={row.failure_phase} />,
          measureValue: (row) => String(row.activity_context ?? (isRecoveredAttempt(row) ? `Attempt ${row.attempt_number ?? "unk"}` : row.failure_phase ?? "unknown"))
        },
        {
          key: "failure_category",
          label: "Category",
          sortable: true,
          autoFit: true,
          maxWidth: 180,
          render: (row) => <CompactValue value={isRecoveredAttempt(row) ? "—" : row.failure_category} />,
          measureValue: (row) => isRecoveredAttempt(row) ? "—" : String(row.failure_category || "unknown")
        },
        {
          key: "activity_message",
          label: "Message",
          sortable: true,
          width: 300,
          render: (row) => <IssuePreview row={{ error_preview: failureActivityMessage(row) }} />,
          measureValue: (row) => String(failureActivityMessage(row) ?? "")
        }
      ]}
      maxRows={maxRows}
      offset={offset}
      onRowClick={onInspect}
      timezoneName={timezoneName}
      className="monitoring-failure-table monitoring-table-one-line"
    />
  );
}

export function RepeatedFailureTable({
  rows,
  maxRows,
  timezoneName
}: {
  rows: Array<Record<string, string | number | null>>;
  maxRows: number;
  timezoneName?: string | null;
}) {
  return (
    <DataTable<Record<string, string | number | null>>
      rows={rows}
      compactNumbers
      compactNumberStyle="infix"
      columns={[
        { key: "failure_category", label: "Category", sortable: true, autoFit: true, maxWidth: 180, measureValue: (row) => String(row.failure_category || "unknown") },
        { key: "failure_phase", label: "Phase", sortable: true, autoFit: true, maxWidth: 112, render: (row) => <FailurePhaseBadge value={row.failure_phase} />, measureValue: (row) => humanize(String(row.failure_phase || "unknown")) },
        { key: "latest_error", label: "Error", sortable: true, minWidth: 240, fillPriority: "last", render: (row) => <IssuePreview row={{ error_preview: row.latest_error }} /> },
        { key: "failed_runs", label: "Runs", sortable: true, autoFit: true, minWidth: 52, maxWidth: 70, render: (row) => <CompactNumberValue value={row.failed_runs} /> },
        { key: "affected_jobs", label: "Jobs", sortable: true, autoFit: true, minWidth: 50, maxWidth: 66, render: (row) => <CompactNumberValue value={row.affected_jobs} /> },
        { key: "affected_dataflows", label: "Flows", sortable: true, autoFit: true, minWidth: 52, maxWidth: 70, render: (row) => <CompactNumberValue value={row.affected_dataflows} /> },
        { key: "latest_time", label: "Latest", sortable: true, autoFit: true, maxWidth: 190, render: (row) => <TableDateTimeValue value={row.latest_time} timezoneName={timezoneName} />, measureValue: (row, activeTimezone) => formatTimestampForDisplay(row.latest_time, activeTimezone, "-") }
      ]}
      maxRows={maxRows}
      timezoneName={timezoneName}
      className="monitoring-failure-table monitoring-table-one-line"
    />
  );
}

export function EndpointImpactTable({
  rows,
  maxRows,
  timezoneName
}: {
  rows: Array<Record<string, string | number | null>>;
  maxRows: number;
  timezoneName?: string | null;
}) {
  return (
    <DataTable<Record<string, string | number | null>>
      rows={rows}
      compactNumbers
      compactNumberStyle="infix"
      columns={[
        { key: "source_name", label: "Route", sortable: true, minWidth: 210, fillPriority: "last", render: (row) => <FailureRouteCell row={row} /> },
        { key: "failed_runs", label: "Runs", sortable: true, autoFit: true, minWidth: 52, maxWidth: 70, render: (row) => <CompactNumberValue value={row.failed_runs} /> },
        { key: "affected_jobs", label: "Jobs", sortable: true, autoFit: true, minWidth: 50, maxWidth: 66, render: (row) => <CompactNumberValue value={row.affected_jobs} /> },
        { key: "latest_time", label: "Latest", sortable: true, autoFit: true, maxWidth: 190, render: (row) => <TableDateTimeValue value={row.latest_time} timezoneName={timezoneName} />, measureValue: (row, activeTimezone) => formatTimestampForDisplay(row.latest_time, activeTimezone, "-") }
      ]}
      maxRows={maxRows}
      timezoneName={timezoneName}
      className="monitoring-failure-table monitoring-table-one-line"
    />
  );
}

export function FailureDataflowNameCell({ row }: { row: Record<string, unknown> }) {
  const name = String(row.dataflow_name || row.dataflow_id || row.job_id || "unknown");
  const details = [
    `Dataflow: ${name}`,
    row.dataflow_id ? `Dataflow ID: ${row.dataflow_id}` : null,
    row.dataflow_run_id ? `Run ID: ${row.dataflow_run_id}` : null,
    row.job_id ? `Job ID: ${row.job_id}` : null
  ].filter(Boolean).join("\n");
  return (
    <Help as="span" className="monitor-inline-cell" title={details}>
      <strong>{name}</strong>
    </Help>
  );
}

export function FailureRouteCell({ row }: { row: Record<string, unknown> }) {
  const source = monitoringDimensionLabel(row.source_name);
  const destination = monitoringDimensionLabel(row.destination_name);
  const sourceFormat = monitoringDimensionLabel(row.source_format ?? row.source_connection_type);
  const destinationFormat = monitoringDimensionLabel(row.destination_format ?? row.destination_connection_type);
  const sourceStageClass = monitoringConnectionStageClass(source);
  const destinationStageClass = monitoringConnectionStageClass(destination);
  return (
    <Help as="span" className="failure-route-cell" title={`${source} -> ${destination}`}>
      <span className={["failure-route-node", sourceStageClass].filter(Boolean).join(" ")}>
        <span className="monitor-endpoint-icon" aria-hidden="true">
          <LineageFormatIcon kind={assetIconKind(sourceFormat || "unknown")} label={sourceFormat || "source"} size={16} />
        </span>
        <strong>{source}</strong>
      </span>
      <span className="failure-route-arrow">→</span>
      <span className={["failure-route-node", destinationStageClass].filter(Boolean).join(" ")}>
        <span className="monitor-endpoint-icon" aria-hidden="true">
          <LineageFormatIcon kind={assetIconKind(destinationFormat || "unknown")} label={destinationFormat || "destination"} size={16} />
        </span>
        <strong>{destination}</strong>
      </span>
    </Help>
  );
}

export function FailurePhaseBadge({ value }: { value: unknown }) {
  const textValue = String(value || "unknown");
  return <span className={`failure-phase-badge is-${textValue}`}>{humanize(textValue)}</span>;
}

export function FailureTrendLegend() {
  return <MonitoringChartLegend label="Failure trend legend" items={[
    ["Dataflows", FAILURE_TREND_COLORS.dataflows],
    ["Jobs", FAILURE_TREND_COLORS.jobs]
  ]} />;
}

export function FailurePhaseLegend() {
  return <MonitoringChartLegend
    label="Failure phase legend"
    items={FAILURE_PHASES.map((phase) => [FAILURE_PHASE_LABELS[phase], FAILURE_PHASE_COLORS[phase]] as const)}
  />;
}
