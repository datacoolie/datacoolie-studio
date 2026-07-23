import { ArrowLeft, ArrowRight, Boxes, BriefcaseBusiness, Check, ChevronRight, Clock3, Copy, FileText, SearchCheck, Workflow, X } from "lucide-react";
import { isValidElement, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { MonitoringRecord } from "../../../shared/api/domainTypes";
import { useDrawerEscape } from "../../../shared/hooks/useDrawerEscape";
import { formatTimestampForDisplay, hasExplicitTimezone, isTimestampFieldName } from "../../../shared/time";
import { lifecycleStatusFromField, lifecycleStatusPresentation, type LifecycleStatus } from "../../../shared/statusPresentation";
import { LineageFormatIcon } from "../../lineage/components/LineageFormatIcon";
import { DataTable, StatusCell, display, formatBytes, formatNumber, formatSeconds, num, type TableSort } from "../MonitoringCharts";
import {
  diagnosticsCategoryLabel,
  diagnosticsEvidenceItems,
  diagnosticsInvestigationActions,
  diagnosticsLinkedJobRow,
  diagnosticsRuleDescription,
  diagnosticsSeverityPresentation,
} from "../diagnosticsPresentation";
import { formatMaintenanceLag, maintenanceFormatIconKind, maintenanceTableHealthClass, maintenanceTableHealthLabel, maintenanceTableHealthTone } from "../maintenancePresentation";
import { formatPhasePercent, monitoringEndpointPresentation, TablePager } from "../components/monitoringPrimitives";
import { SystemLogViewer } from "../SystemLogViewer";
import { DataflowPhaseContribution, DataflowPhaseKey, DataflowRouteEndpoint, GroupedDetailCard, dataflowEndpointSummary, dataflowPhaseBottleneck, dataflowPhaseSegments, firstValue, hasValue, phaseRuntimeStatusClass } from "./detailPrimitives";
import { PerformanceDetailSections } from "./PerformanceDetails";
import { FailureEvidenceSections } from "./FailureDetails";

export function DataflowRunSummary({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  const source = dataflowEndpointSummary(row, "source");
  const destination = dataflowEndpointSummary(row, "destination");
  const loadType = firstValue(row, ["destination_load_type", "load_type", "destination_operation_type"]);
  const bytesAdded = num(row, "destination_bytes_added");
  const bytesRemoved = num(row, "destination_bytes_removed");
  const bottleneck = dataflowPhaseBottleneck(row);
  return (
    <section className="monitoring-detail-section monitoring-dataflow-summary-section">
      <div className="monitoring-dataflow-route-card">
        <DataflowRouteEndpoint direction="source" endpoint={source} />
        <ArrowRight className="monitoring-dataflow-route-arrow" size={18} aria-hidden="true" />
        <DataflowRouteEndpoint direction="destination" endpoint={destination} />
        <div className="monitoring-dataflow-route-context">
          <span>{String(row.stage || "unknown stage")}</span>
          <span>{String(row.operation_type || "unknown operation")}</span>
          <span>{String(loadType || "unknown load type")}</span>
        </div>
      </div>
      <div className="monitoring-dataflow-summary-grid">
        <GroupedDetailCard
          title="Execution"
          className="monitoring-dataflow-execution-card"
          rows={[
            ["Status", <StatusCell row={row} />],
            ["Run ID", row.dataflow_run_id],
            ["Start", row.start_time, "start_time"],
            ["End", row.end_time, "end_time"],
            ["Duration", row.duration_seconds, "duration_seconds"],
            ["Retry attempts", row.retry_attempts],
          ]}
          showEmpty
          timezoneName={timezoneName}
        />
        <GroupedDetailCard
          title="Phase health"
          className={`monitoring-dataflow-phase-card${bottleneck ? ` phase-health-${bottleneck.phase}` : ""}`}
          rows={[
            ["Bottleneck", <DataflowPhaseBottleneck row={row} />],
            ["Contribution", <DataflowPhaseContribution row={row} />, "phase_contribution"],
            ["Source", row.source_duration_seconds, "source_duration_seconds"],
            ["Transform", row.transform_duration_seconds, "transform_duration_seconds"],
            ["Destination", row.destination_duration_seconds, "destination_duration_seconds"],
            ["Overhead", row.overhead_duration_seconds, "overhead_duration_seconds"],
          ]}
          showEmpty
          timezoneName={timezoneName}
        />
        <GroupedDetailCard
          title="Workload"
          className="monitoring-dataflow-workload-card"
          rows={[
            ["Rows read", row.source_rows_read, "source_rows_read"],
            ["Rows written", row.destination_rows_written, "destination_rows_written"],
            ["Bytes added", row.destination_bytes_added, "destination_bytes_added"],
            ["Bytes removed", row.destination_bytes_removed, "destination_bytes_removed"],
            ["Net bytes", bytesAdded - bytesRemoved, "net_bytes"],
            ["Watermark", row.movement_state],
          ]}
          showEmpty
          timezoneName={timezoneName}
        />
      </div>
    </section>
  );
}

export function DataflowDetailSections({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  return (
    <>
      <DataflowRunSummary row={row} timezoneName={timezoneName} />

      {hasPerformanceEvidence(row) ? <PerformanceDetailSections row={row} /> : null}

      <FailureEvidenceSections row={row} timezoneName={timezoneName} />

      <section className="monitoring-detail-section monitoring-dataflow-section monitoring-dataflow-section-configuration">
        <h3>Dataflow configuration</h3>
        <div className="monitoring-dataflow-detail-grid">
          <GroupedDetailCard
            title="Identity and configuration"
            className="monitoring-dataflow-config-card"
            rows={[
              ["Dataflow ID", row.dataflow_id],
              ["Workspace ID", row.workspace_id],
              ["Name", row.dataflow_name],
              ["Description", row.dataflow_description],
              ["Stage", row.stage],
              ["Group number", row.group_number],
              ["Execution order", row.execution_order],
              ["Processing mode", row.processing_mode],
              ["Active", row.is_active],
              ["Configure", row.configure, "configure"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
        </div>
      </section>

      <section className="monitoring-detail-section monitoring-dataflow-section monitoring-dataflow-section-source">
        <h3>Source</h3>
        <div className="monitoring-dataflow-detail-grid">
          <GroupedDetailCard
            title="Master data"
            rows={[
              ["Source ID", row.source_id],
              ["Source name", firstValue(row, ["source_name", "source_connection_name"])],
              ["Connection type", row.source_connection_type],
              ["Format", row.source_format],
              ["Catalog", row.source_catalog],
              ["Database", row.source_database],
              ["Schema", row.source_schema],
              ["Table", row.source_table],
              ["Full table", row.source_full_table],
              ["Path", row.source_path],
              ["Query", row.source_query, "source_query"],
              ["Python function", row.source_python_function],
              ["Watermark columns", row.source_watermark_columns, "source_watermark_columns"],
              ["Filter", row.source_filter_expression, "source_filter_expression"],
              ["Configure", row.source_configure, "source_configure"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Runtime info"
            className={phaseRuntimeStatusClass(row.source_status)}
            rows={[
              ["Start", row.source_start_time, "source_start_time"],
              ["End", row.source_end_time, "source_end_time"],
              ["Duration", row.source_duration_seconds, "source_duration_seconds"],
              ["Status", row.source_status, "source_status"],
              ["Error", row.source_error_message, "source_error_message"],
              ["Rows read", row.source_rows_read, "source_rows_read"],
              ["Action", row.source_action, "source_action"],
              ["Watermark before", row.source_watermark_before, "source_watermark_before"],
              ["Watermark after", row.source_watermark_after, "source_watermark_after"],
              ["Watermark effective", row.source_watermark_effective, "source_watermark_effective"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
        </div>
      </section>

      <section className="monitoring-detail-section monitoring-dataflow-section monitoring-dataflow-section-transform">
        <h3>Transform</h3>
        <div className="monitoring-dataflow-detail-grid">
          <GroupedDetailCard
            title="Master data"
            rows={[
              ["Deduplicate", row.transform_deduplicate_columns, "transform_deduplicate_columns"],
              ["Latest data", row.transform_latest_data_columns, "transform_latest_data_columns"],
              ["Filter", row.transform_filter_expression, "transform_filter_expression"],
              ["Additional columns", row.transform_additional_columns, "transform_additional_columns"],
              ["Schema hints", row.transform_schema_hints, "transform_schema_hints"],
              ["Configure", row.transform_configure, "transform_configure"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Runtime info"
            className={phaseRuntimeStatusClass(row.transform_status)}
            rows={[
              ["Start", row.transform_start_time, "transform_start_time"],
              ["End", row.transform_end_time, "transform_end_time"],
              ["Duration", row.transform_duration_seconds, "transform_duration_seconds"],
              ["Status", row.transform_status, "transform_status"],
              ["Error", row.transform_error_message, "transform_error_message"],
              ["Transformers applied", row.transformers_applied, "transformers_applied"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
        </div>
      </section>

      <section className="monitoring-detail-section monitoring-dataflow-section monitoring-dataflow-section-destination">
        <h3>Destination</h3>
        <div className="monitoring-dataflow-detail-grid">
          <GroupedDetailCard
            title="Master data"
            rows={[
              ["Destination ID", row.destination_id],
              ["Destination name", firstValue(row, ["destination_name", "destination_connection_name"])],
              ["Connection type", row.destination_connection_type],
              ["Format", row.destination_format],
              ["Catalog", row.destination_catalog],
              ["Database", row.destination_database],
              ["Schema", row.destination_schema],
              ["Table", row.destination_table],
              ["Full table", row.destination_full_table],
              ["Path", row.destination_path],
              ["Load type", row.destination_load_type],
              ["Merge keys", row.destination_merge_keys, "destination_merge_keys"],
              ["Partition columns", row.destination_partition_columns, "destination_partition_columns"],
              ["Configure", row.destination_configure, "destination_configure"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Runtime info"
            className={phaseRuntimeStatusClass(row.destination_status)}
            rows={[
              ["Start", row.destination_start_time, "destination_start_time"],
              ["End", row.destination_end_time, "destination_end_time"],
              ["Duration", row.destination_duration_seconds, "destination_duration_seconds"],
              ["Status", row.destination_status, "destination_status"],
              ["Error", row.destination_error_message, "destination_error_message"],
              ["Operation type", row.destination_operation_type],
              ["Written", row.destination_rows_written, "destination_rows_written"],
              ["Inserted", row.destination_rows_inserted, "destination_rows_inserted"],
              ["Updated", row.destination_rows_updated, "destination_rows_updated"],
              ["Deleted", row.destination_rows_deleted, "destination_rows_deleted"],
              ["Files added", row.destination_files_added],
              ["Files removed", row.destination_files_removed],
              ["Bytes added", row.destination_bytes_added, "destination_bytes_added"],
              ["Bytes removed", row.destination_bytes_removed, "destination_bytes_removed"],
              ["Bytes saved", row.destination_bytes_saved, "destination_bytes_saved"],
              ["Operation details", row.destination_operation_details, "destination_operation_details"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
        </div>
      </section>
    </>
  );
}

export function hasPerformanceEvidence(row: Record<string, unknown>) {
  return [
    row.performance_bottleneck_phase,
    row.performance_candidate_code,
    row.performance_candidate_reason,
    row.performance_candidate_priority,
    row.performance_rows_processed,
    row.performance_rows_per_second,
    row.performance_overhead_ratio,
    row.performance_dominant_phase_ratio,
  ].some(hasValue);
}

export function DataflowPhaseBottleneck({ row }: { row: Record<string, unknown> }) {
  const bottleneck = dataflowPhaseBottleneck(row);
  if (!bottleneck) return <span className="monitor-muted">-</span>;
  return (
    <span className="monitoring-phase-bottleneck">
      <span className={`phase-text-${bottleneck.phase}`}>{bottleneck.label}</span>
      {" "}
      <PhaseContributionSummary row={row} />
    </span>
  );
}

export function PhaseContributionSummary({ row }: { row: Record<string, unknown> }) {
  const segments = dataflowPhaseSegments(row);
  if (!segments.length) return null;
  return (
    <span className="monitoring-phase-bottleneck-summary" aria-label="Phase contribution summary">
      <span className="monitoring-phase-summary-punctuation">(</span>
      {segments.map((segment, index) => (
        <span key={segment.phase} className={`phase-text-${segment.phase}`}>
          {phaseShortLabel(segment.phase)} {formatPhasePercent(segment.percent)}
        </span>
      ))}
      <span className="monitoring-phase-summary-punctuation">)</span>
    </span>
  );
}

export function phaseShortLabel(phase: DataflowPhaseKey) {
  if (phase === "source") return "S";
  if (phase === "transform") return "T";
  if (phase === "destination") return "D";
  return "O";
}
