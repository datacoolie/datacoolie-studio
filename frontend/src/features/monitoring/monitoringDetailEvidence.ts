import type { MonitoringDetailKind } from "./MonitoringDetailDrawer";
import type { TableSort } from "./MonitoringCharts";

export interface MonitoringDetailTarget {
  kind: MonitoringDetailKind;
  row: Record<string, unknown>;
}

export interface MonitoringDetailEvidencePage {
  limit: number;
  offset: number;
  sort: TableSort;
}

export function monitoringDetailEvidenceRequest(
  detail: MonitoringDetailTarget | null,
  baseParams: Record<string, string | number | undefined>,
  page: MonitoringDetailEvidencePage,
) {
  if (!detail || !isPagedEvidenceKind(detail.kind)) return null;
  const investigateKind = detail.kind === "job"
    ? "job_id"
    : detail.kind === "maintenance"
      ? "destination_table"
      : "dataflow";
  const investigateValue = detailEvidenceValue(detail);
  if (!investigateValue) return null;
  return {
    params: {
      ...baseParams,
      ...(detail.kind === "maintenance" ? { operationType: "all" } : {}),
      limit: page.limit,
      offset: page.offset,
      sortBy: page.sort.sortBy,
      sortDir: page.sort.sortDir,
      investigateKind,
      investigateValue,
    },
    key: `${detail.kind}:${investigateValue}`,
  };
}

export function isPagedEvidenceKind(kind: MonitoringDetailKind) {
  return kind === "job" || kind === "freshness" || kind === "maintenance" || kind === "volume";
}

function detailEvidenceValue(detail: MonitoringDetailTarget) {
  if (detail.kind === "job") return String(detail.row.job_id ?? "").trim();
  if (detail.kind === "maintenance") {
    return String(
      detail.row.target
      ?? detail.row.table
      ?? detail.row.destination_full_table
      ?? detail.row.destination_table
      ?? detail.row.destination_path
      ?? "",
    ).trim();
  }
  return String(detail.row.dataflow_id ?? "").trim();
}
