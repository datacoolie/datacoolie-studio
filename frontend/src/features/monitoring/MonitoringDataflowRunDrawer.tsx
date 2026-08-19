import { useQuery } from "@tanstack/react-query";
import type { MonitoringRecord } from "../../shared/api/domainTypes";
import { MonitoringDetailDrawer, type MonitoringDetailDrawerProps } from "./MonitoringDetailDrawer";
import { mergeDataflowRunDetail } from "./monitoringDetailEvidence";
import { monitoringDataflowRunDetailOptions } from "./monitoringQueries";

export interface MonitoringDataflowRunDrawerProps extends Omit<MonitoringDetailDrawerProps, "kind" | "row" | "environmentId"> {
  environmentId: number;
  row: MonitoringRecord;
}

export function MonitoringDataflowRunDrawer({ environmentId, row, ...drawerProps }: MonitoringDataflowRunDrawerProps) {
  const dataflowRunId = String(row.dataflow_run_id ?? "").trim();
  const detailQuery = useQuery(monitoringDataflowRunDetailOptions(environmentId, dataflowRunId));

  return (
    <MonitoringDetailDrawer
      {...drawerProps}
      kind="dataflow"
      row={mergeDataflowRunDetail(row, detailQuery.data)}
      environmentId={environmentId}
    />
  );
}
