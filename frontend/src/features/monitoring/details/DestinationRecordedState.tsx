import { CompactBytesValue, CompactNumberValue, metricValue } from "../components/monitoringPresentation";
import { formatTimestampForDisplay } from "../../../shared/time";
import { GroupedDetailCard, type DetailRow } from "./detailPrimitives";
import { Help } from "../../../shared/components/Help";

type Measurement = { value: string; unit: string; meaning: string };
type RecordedState = {
  availability: "recorded" | "partial" | "not_recorded";
  observed_at: string | null;
  revision: string | null;
  kind: string | null;
  timestamp_origin: string;
  conflict: boolean;
  measurements: Partial<Record<"rows" | "files" | "bytes", Measurement>>;
  provenance: Record<string, unknown>;
};

const meanings: Record<string, { label: string; description: string }> = {
  live_rows: { label: "Rows", description: "Recorded live row count." },
  live_data_file_records_before_deletes: { label: "Data-file records", description: "Iceberg records in live data files before applying row deletes; this is not the visible table row count." },
  live_data_files: { label: "Data files", description: "Live data files at the recorded observation." },
  live_files: { label: "Files", description: "Recorded live files at this observation." },
  live_files_including_delete_files: { label: "Files (incl. deletes)", description: "Live files including delete files." },
  storage_bytes: { label: "Bytes", description: "Recorded table/object storage size in bytes." },
  live_files_bytes_including_delete_files: { label: "Bytes (incl. deletes)", description: "Iceberg total size of live files, including delete files." },
};

export function recordedDestinationState(row: Record<string, unknown>): RecordedState | null {
  const value = row.recorded_state;
  return value && typeof value === "object" && !Array.isArray(value) ? value as RecordedState : null;
}

export function DestinationRecordedState({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  const state = recordedDestinationState(row);
  if (!state || state.availability === "not_recorded") return <p className="destination-drawer-empty">No object-state measurements recorded in the current filters.</p>;
  const rows: DetailRow[] = [
    ["Observed at", <Help title={state.timestamp_origin === "run" ? "Observation timestamp was not recorded; using the recording run time." : "Timestamp of this recorded observation."}>{formatTimestampForDisplay(state.observed_at, timezoneName, "—")}</Help>],
    ["Revision", state.revision ?? "—"],
    ["Evidence", state.kind === "iceberg_snapshot" ? "Iceberg snapshot" : state.kind === "delta_table_detail" ? "Delta table detail" : "Recorded object statistics"],
  ];
  for (const unit of ["rows", "files", "bytes"] as const) {
    const measurement = state.measurements?.[unit];
    const meaning = measurement ? meanings[measurement.meaning] : null;
    const value = measurement ? metricValue({ value: measurement.value }, "value") : null;
    rows.push([meaning?.label ?? unit.charAt(0).toUpperCase()+unit.slice(1), <Help title={meaning?.description ?? "Not recorded in this observation; values are not combined from other observations."}>{unit === "bytes" ? <CompactBytesValue value={value} fallback="—" /> : <CompactNumberValue value={value} fallback="—" />}</Help>]);
  }
  if (state.conflict) rows.push(["Evidence conflict", <span className="performance-reason-bad">Conflicting observations at this time</span>]);
  return <><GroupedDetailCard title="Recorded measurements" rows={rows} showEmpty />
    <small className="destination-drawer-caption">{state.availability === "partial" ? "Partial measurements. " : ""}One observation in the filter; external storage has not been checked.</small></>;
}

export function DestinationStateProvenance({ row }: { row: Record<string, unknown> }) {
  const state = recordedDestinationState(row);
  if (!state || state.availability === "not_recorded") return null;
  return <GroupedDetailCard title="Object-state provenance" rows={[
    ["Source", state.provenance.source_id], ["Session", state.provenance.log_session_id],
    ["Job", state.provenance.job_id], ["Run", state.provenance.dataflow_run_id],
    ["File", state.provenance.file_uri], ["Record", state.provenance.record_line],
    ["Entry", state.provenance.entry_path], ["Time origin", state.timestamp_origin],
  ]} showEmpty />;
}
