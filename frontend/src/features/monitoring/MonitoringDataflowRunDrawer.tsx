import { useMemo } from "react";
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import type { MonitoringRecord } from "../../shared/api/domainTypes";
import { MonitoringDetailDrawer, type MonitoringDetailDrawerProps } from "./MonitoringDetailDrawer";
import { mergeDataflowRunDetail, validateMonitoringRegistryRunEvidence } from "./monitoringDetailEvidence";
import { monitoringDataflowRunDetailOptions, monitoringRowSessionScope, type MonitoringDataflowRunDetailEnvelope, type MonitoringRegistryRunReference } from "./monitoringQueries";

export interface MonitoringDataflowRunDrawerProps extends Omit<MonitoringDetailDrawerProps, "kind" | "row" | "environmentId"> {
  environmentId: number;
  row: MonitoringRecord;
  registryReference?: MonitoringRegistryRunReference;
  onRegistryProvenanceInvalid?: (reference: MonitoringRegistryRunReference, reason: string) => void;
}

export function MonitoringDataflowRunDrawer({ environmentId, row, registryReference, onRegistryProvenanceInvalid, ...drawerProps }: MonitoringDataflowRunDrawerProps) {
  const dataflowRunId = String(row.dataflow_run_id ?? "");
  const sessionScope = monitoringRowSessionScope(row);
  const sessionId = sessionScope === "exact" ? String(row.log_session_id ?? "") : undefined;
  const detailQuery = useQuery(monitoringDataflowRunDetailOptions(environmentId, dataflowRunId, sessionId, row._source_id == null ? undefined : Number(row._source_id), sessionScope, registryReference));

  const exactEnvelope = registryReference && detailQuery.data && typeof detailQuery.data === "object" && "summary" in detailQuery.data
    ? detailQuery.data as MonitoringDataflowRunDetailEnvelope
    : null;
  const canonicalRow: Record<string, unknown> | null | undefined = exactEnvelope
    ? exactEnvelope.row
    : detailQuery.data && typeof detailQuery.data === "object" && !("summary" in detailQuery.data)
      ? detailQuery.data as MonitoringRecord
      : null;
  const provenance = useMemo(
    () => registryReference
      ? validateMonitoringRegistryRunEvidence(registryReference, exactEnvelope ?? canonicalRow)
      : { ok: true as const, row: canonicalRow },
    [canonicalRow, exactEnvelope, registryReference],
  );
  const canMergeCanonicalRow = provenance.ok && canonicalRow != null;

  const detailRow = useMemo(() => mergeDataflowRunDetail(row, canMergeCanonicalRow ? canonicalRow : null), [canMergeCanonicalRow, canonicalRow, row]);

  useEffect(() => {
    if (!registryReference || !detailQuery.isSuccess || provenance.ok) return;
    onRegistryProvenanceInvalid?.(registryReference, provenance.reason);
  }, [detailQuery.isSuccess, onRegistryProvenanceInvalid, provenance, registryReference]);

  return (
    <MonitoringDetailDrawer
      {...drawerProps}
      key={`${row._source_id ?? "all"}:${sessionScope}:${sessionId ?? ""}:${dataflowRunId}`}
      kind="dataflow"
      dataflowDetailAvailability={detailQuery.isPending ? "loading" : detailQuery.isError || (registryReference && !provenance.ok) ? "error" : canonicalRow == null ? "not-found" : "ready"}
      onRetryDataflowDetail={() => { void detailQuery.refetch(); }}
      row={detailRow}
      environmentId={environmentId}
    />
  );
}
