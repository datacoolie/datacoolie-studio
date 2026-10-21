import type { MonitoringRecord } from "../../../shared/api/domainTypes";
import { DataTable, StatusCell, type TableColumn, type TableSort } from "../../../shared/components/DataTable";
import { Help } from "../../../shared/components/Help";
import { CompactNumberValue, DataflowPhaseCell, formatCompact, formatNumber, formatSeconds, metricValue, TablePager } from "../components/monitoringPresentation";
import { FreshnessRunTimeCell, IssueCell } from "./detailPrimitives";
import { StoryDisclosure, StoryDuration } from "./DataflowStoryPrimitives";
import { phaseContributionBottleneck, rowPhaseContribution } from "../monitoringPhaseContribution";

export interface JobExecutionProps {
  rows?: MonitoringRecord[]; total?: number; offset?: number; limit?: number; sort?: TableSort;
  loading?: boolean; error?: string | null; timezoneName?: string | null;
  onRetry?: () => void; onPageChange?: (offset: number) => void; onPageSizeChange?: (limit: number) => void;
  onSort?: (sort: TableSort) => void; onOpenDataflow?: (row: MonitoringRecord) => void;
}
export function JobExecution({ rows = [], total = 0, offset = 0, limit = 100, sort, loading, error, timezoneName, onRetry, onPageChange, onPageSizeChange, onSort, onOpenDataflow }: JobExecutionProps) {
  return <StoryDisclosure title="Execution" defaultOpen className="job-story-execution">
    {() => <>
    {error ? <div className="dataflow-story-availability" role="alert">Child runs could not be loaded. {onRetry ? <button type="button" className="small-button" onClick={onRetry}>Retry child runs</button> : null}</div> : null}
    {loading ? <p role="status" className="job-story-note">Loading child runs…</p> : null}
    <div className={`job-execution-table-group${rows.length ? " has-rows" : " is-empty"}`}>
      <TablePager limit={limit} offset={offset} loadedRows={rows.length} totalRows={total} loading={Boolean(loading)} onPageChange={value=>onPageChange?.(value)} onPageSizeChange={onPageSizeChange ? value=>onPageSizeChange(value) : undefined} />
      {rows.length ? <DataTable rows={rows} columns={jobChildDataflowColumns(timezoneName)} compactNumbers compactNumberStyle="infix" maxRows={limit} offset={0} onRowClick={onOpenDataflow} sort={sort} onSort={onSort} fixedLayout className="monitoring-child-dataflows-table" timezoneName={timezoneName} /> : !loading && !error ? <p className="job-story-note">No child runs recorded.</p> : null}
    </div>
  </>}</StoryDisclosure>;
}

export function jobChildDataflowColumns(timezoneName?: string | null): TableColumn<MonitoringRecord>[] {
  return [
    { key: "dataflow_name", label: "Dataflow", sortable: true, width: 160 },
    {
      key: "stage_operation",
      label: "Stage / operation",
      sortable: true,
      sortKey: "stage",
      autoFit: true,
      minWidth: 112,
      maxWidth: 180,
      render: (child) => <ChildDataflowStageOperationCell row={child} />,
      measureValue: childDataflowStageOperationLines,
    },
    {
      key: "start_time",
      label: "Time",
      sortable: true,
      width: 170,
      render: (child) => <FreshnessRunTimeCell row={child} timezoneName={timezoneName} />,
    },
    {
      key: "phase_health",
      label: "Phase",
      sortable: true,
      autoFit: true,
      minWidth: 100,
      maxWidth: 180,
      render: child => <DataflowPhaseCell row={child} unknownLabel="unk" />,
      measureValue: childDataflowPhaseLines,
    },
    { key: "status", label: "Status", sortable: true, autoFit: true, minWidth: 68, maxWidth: 124, render: (child) => <StatusCell row={child} />, measureValue: childDataflowStatusLines },
    { key: "duration_seconds", label: "Duration", sortable: true, autoFit: true, minWidth: 68, maxWidth: 104, render: (child) => <StoryDuration value={child.duration_seconds} unknownLabel="unk" />, measureValue: childDataflowDurationLines },
    {
      key: "row_counts",
      label: "Rows",
      sortable: true,
      sortKey: "source_rows_read",
      autoFit: true,
      minWidth: 96,
      maxWidth: 160,
      render: (child) => <ChildDataflowRowsCell row={child} />,
      measureValue: childDataflowCompactRowLines,
    },
    { key: "message", label: "Message", sortable: true, width: 240, className: "monitoring-child-issue-column", render: (child) => <IssueCell row={child} /> },
  ];
}

export function childDataflowStageOperationLines(row: MonitoringRecord) {
  return [String(row.stage || "-"), String(row.operation_type || "-")];
}

export function childDataflowRowLines(row: MonitoringRecord) {
  return [
    `${formatNumber(metricValue(row, "source_rows_read"))} read`,
    `${formatNumber(metricValue(row, "destination_rows_written"))} written`,
  ];
}

export function childDataflowPhaseLines(row: MonitoringRecord) {
  const { root, timing } = rowPhaseContribution(row);
  const bottleneck = phaseContributionBottleneck(root, timing);
  return [bottleneck.phases.length ? bottleneck.label : "unk"];
}

export function childDataflowStatusLines(row: MonitoringRecord) {
  return [typeof row.status === "string" && row.status.trim() ? row.status : "unknown"];
}

export function childDataflowDurationLines(row: MonitoringRecord) {
  const value = metricValue(row, "duration_seconds");
  if (value === null || value === undefined || value === "") return ["unk"];
  const seconds = Number(value);
  return [Number.isFinite(seconds) ? formatSeconds(seconds) : "unk"];
}

function childDataflowCompactRowLines(row: MonitoringRecord) {
  return [
    `${formatCompact(metricValue(row, "source_rows_read"))} read`,
    `${formatCompact(metricValue(row, "destination_rows_written"))} written`,
  ];
}

function ChildDataflowStageOperationCell({ row }: { row: MonitoringRecord }) {
  const [stage, operation] = childDataflowStageOperationLines(row);
  return (
    <Help className="freshness-run-stack-cell" title={`Stage: ${stage}\nOperation: ${operation}`}>
      <strong>{stage}</strong>
      <small>{operation}</small>
    </Help>
  );
}

function ChildDataflowRowsCell({ row }: { row: MonitoringRecord }) {
  const rowsRead = metricValue(row, "source_rows_read");
  const rowsWritten = metricValue(row, "destination_rows_written");
  return (
    <Help className="freshness-run-stack-cell job-execution-rows-cell" title={`Rows read: ${formatNumber(rowsRead)}\nRows written: ${formatNumber(rowsWritten)}`}>
      <span><CompactNumberValue value={rowsRead} /> read</span>
      <span><CompactNumberValue value={rowsWritten} /> written</span>
    </Help>
  );
}
