import { lazy, Suspense, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { EmptyState } from "../../shared/components/EmptyState";
import { OperationNotification } from "../../shared/components/OperationNotification";
import { ApiRequestError } from "../../shared/lib/errors";
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

  useEffect(() => {
    setNoticeDismissed(false);
  }, [environmentId, reportError]);

  useEffect(() => {
    writeFiltersToSearch(filters);
  }, [filters]);

  useEffect(() => {
    void preloadMonitoringPage(activePage).catch(() => undefined);
  }, [activePage]);

  return (
    <>
      {rebuildRequired && !noticeDismissed ? (
        <OperationNotification
          notice={{
            tone: "warning",
            title: "Monitoring analytics need to be rebuilt",
            detail: "Open Sources and sync the Log sources before loading Monitoring again.",
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
        onRetryReport={() => void reportQuery.refetch()}
        onOpenSources={onOpenSources}
        filterOptions={filterOptionsQuery.data ?? null}
      />
      </Suspense>
    </>
  );
}
