import { lazy, Suspense, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { EmptyState } from "../../shared/components/EmptyState";
import { OperationNotification } from "../../shared/components/OperationNotification";
import { ApiRequestError } from "../../shared/lib/errors";
import { api } from "../../shared/api/client";
import { monitoringFilterOptionsOptions, monitoringReportOptions } from "./monitoringQueries";
import { filtersFromSearch, type MonitoringTabKey, writeFiltersToSearch } from "./monitoringFilters";
import { preloadMonitoringPage } from "./monitoringPageModules";

const MonitoringView = lazy(() => import("./MonitoringView").then((module) => ({ default: module.MonitoringView })));

export function MonitoringRoute({
  environmentId,
  activePage,
  onPageChange,
  onOpenSources,
}: {
  environmentId: number;
  activePage: MonitoringTabKey;
  onPageChange: (page: MonitoringTabKey) => void;
  onOpenSources: () => void;
}) {
  const [filters, setFilters] = useState(() => filtersFromSearch(window.location.search));
  const reportQuery = useQuery(monitoringReportOptions(environmentId, activePage, filters));
  const filterOptionsQuery = useQuery(monitoringFilterOptionsOptions(environmentId));
  const [noticeDismissed, setNoticeDismissed] = useState(false);
  const reportError = reportQuery.error;
  const rebuildRequired = reportError instanceof ApiRequestError
    && reportError.code === "analytics_rebuild_required";
  const rebuildReason = rebuildRequired && typeof reportError.detail === "object"
    ? String(reportError.detail?.reason ?? "")
    : "";
  const upgradeInProgress = rebuildReason === "analytics_upgrade_in_progress";
  const refetchReport = reportQuery.refetch;

  useEffect(() => {
    setNoticeDismissed(false);
  }, [environmentId, reportError]);

  useEffect(() => {
    writeFiltersToSearch(filters);
  }, [filters]);

  useEffect(() => {
    void preloadMonitoringPage(activePage).catch(() => undefined);
  }, [activePage]);

  useEffect(() => {
    if (!upgradeInProgress) return undefined;
    const timer = window.setTimeout(() => {
      void refetchReport();
    }, 2_000);
    return () => window.clearTimeout(timer);
  }, [refetchReport, upgradeInProgress]);

  const retryUpgrade = async () => {
    await api.retryAnalyticsUpgrade();
    await reportQuery.refetch();
  };

  return (
    <>
      {rebuildRequired && !noticeDismissed ? (
        <OperationNotification
          notice={{
            tone: "warning",
            title: upgradeInProgress ? "Monitoring analytics are upgrading" : "Monitoring analytics need attention",
            detail: upgradeInProgress
              ? "Studio is rebuilding the DuckDB cache automatically. Monitoring will refresh when publication completes."
              : rebuildReason === "analytics_upgrade_failed"
                ? "The previous cache was preserved. Studio will retry automatically, or you can retry now."
                : "Open Sources and sync the Log sources before loading Monitoring again.",
          }}
          onClose={() => setNoticeDismissed(true)}
        />
      ) : null}
      <Suspense fallback={<EmptyState title="Loading Monitoring…" />}>
      <MonitoringView
        environmentId={environmentId}
        activePage={activePage}
        onPageChange={onPageChange}
        filters={filters}
        onFiltersChange={setFilters}
        reportData={reportQuery.data ?? null}
        reportLoading={reportQuery.isFetching}
        reportError={reportError instanceof Error ? reportError.message : reportError ? String(reportError) : null}
        reportErrorCode={rebuildRequired ? reportError.code ?? null : null}
        reportErrorReason={rebuildReason || null}
        onRetryReport={() => void reportQuery.refetch()}
        onRetryUpgrade={() => void retryUpgrade().catch(() => undefined)}
        onOpenSources={onOpenSources}
        filterOptions={filterOptionsQuery.data ?? null}
      />
      </Suspense>
    </>
  );
}
