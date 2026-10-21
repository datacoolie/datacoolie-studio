import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { MonitoringRecord, MonitoringRegistryResponse } from "../../shared/api/domainTypes";
import type { TableSort } from "../../shared/components/DataTable";
import { environmentQueryKeys } from "../environments/environmentQueries";
import { MonitoringDetailDrawer, type MonitoringDetailDrawerProps } from "./MonitoringDetailDrawer";
import {
  isMonitoringRegistryOriginValid,
  monitoringDataflowRegistryOptions,
  monitoringRegistryOriginForResponse,
  monitoringRegistryOriginsMatch,
  monitoringRegistryResponseMatchesOrigin,
  monitoringRegistryScope,
  monitoringRegistryVersionToken,
  sameMonitoringRegistryInstant,
  type MonitoringRegistryOrigin,
} from "./monitoringQueries";
import type { MonitoringFilters } from "./monitoringFilters";

type RegistrySection = "read-watermark" | "configuration" | "technical";
type RegistryResponse = MonitoringRegistryResponse;

type RegistryComposition = {
  scope: string;
  origin: MonitoringRegistryOrigin | null;
  overview: RegistryResponse | null;
  sections: Array<RegistryResponse | undefined>;
};

export interface MonitoringDataflowRegistryDrawerProps extends Omit<MonitoringDetailDrawerProps, "kind" | "row" | "environmentId"> {
  environmentId: number;
  row: MonitoringRecord;
  filters: MonitoringFilters;
  asOf: string;
  presentationScope?: string | null;
  parentOrigin?: MonitoringRegistryOrigin | null;
}

export function MonitoringDataflowRegistryDrawer({
  environmentId,
  row,
  filters,
  asOf,
  presentationScope,
  parentOrigin,
  ...drawerProps
}: MonitoringDataflowRegistryDrawerProps) {
  const queryClient = useQueryClient();
  const dataflowIdValue = String(row.dataflow_id ?? "");
  const dataflowId = dataflowIdValue.trim() ? dataflowIdValue : "";
  const registryPresentationScope = presentationScope ?? parentOrigin?.presentationScope ?? "";
  const registryGeneration = isMonitoringRegistryOriginValid(parentOrigin) ? monitoringRegistryVersionToken(parentOrigin) : "";
  const filterScope = monitoringRegistryScope(filters);
  const compositionScope = JSON.stringify([environmentId, dataflowId, filterScope, registryPresentationScope, parentOrigin?.identityVersion ?? null]);
  const [openSections, setOpenSections] = useState<ReadonlySet<RegistrySection>>(() => new Set());
  const [executionOffset, setExecutionOffset] = useState(0);
  const [executionLimit, setExecutionLimit] = useState(100);
  const [executionSort, setExecutionSort] = useState<TableSort>({ sortBy: "event_time", sortDir: "desc" });
  const [runtimeOffset, setRuntimeOffset] = useState(0);
  const [runtimeLimit, setRuntimeLimit] = useState(100);
  const [runtimeSort, setRuntimeSort] = useState<TableSort>({ sortBy: "run_count", sortDir: "desc" });

  useEffect(() => {
    setOpenSections(new Set());
    setExecutionOffset(0);
    setExecutionSort({ sortBy: "event_time", sortDir: "desc" });
    setRuntimeOffset(0);
    setRuntimeSort({ sortBy: "run_count", sortDir: "desc" });
  }, [compositionScope]);

  const overviewPagination = { limit: 1, offset: 0, sort: { sortBy: "latest_run_at", sortDir: "desc" as const } };
  const detailQuery = useQuery(monitoringDataflowRegistryOptions(
    environmentId,
    filters,
    overviewPagination,
    dataflowId || null,
    "overview",
    asOf,
    "all",
    registryPresentationScope,
    registryGeneration,
  ));
  const sectionQuery = useQuery({
    ...monitoringDataflowRegistryOptions(
      environmentId,
      filters,
      overviewPagination,
      dataflowId || null,
      "read-watermark",
      asOf,
      "all",
      registryPresentationScope,
      registryGeneration,
    ),
    enabled: Boolean(dataflowId && openSections.has("read-watermark")),
  });
  const configurationQuery = useQuery({
    ...monitoringDataflowRegistryOptions(
      environmentId,
      filters,
      overviewPagination,
      dataflowId || null,
      "configuration",
      asOf,
      "all",
      registryPresentationScope,
      registryGeneration,
    ),
    enabled: Boolean(dataflowId && openSections.has("configuration")),
  });
  const technicalQuery = useQuery({
    ...monitoringDataflowRegistryOptions(
      environmentId,
      filters,
      overviewPagination,
      dataflowId || null,
      "technical",
      asOf,
      "all",
      registryPresentationScope,
      registryGeneration,
    ),
    enabled: Boolean(dataflowId && openSections.has("technical")),
  });
  const executionQuery = useQuery({
    ...monitoringDataflowRegistryOptions(
      environmentId,
      filters,
      { limit: executionLimit, offset: executionOffset, sort: executionSort },
      dataflowId || null,
      "execution",
      asOf,
      "all",
      registryPresentationScope,
      registryGeneration,
    ),
    enabled: Boolean(dataflowId),
  });
  const runtimeQuery = useQuery({
    ...monitoringDataflowRegistryOptions(
      environmentId,
      filters,
      { limit: runtimeLimit, offset: runtimeOffset, sort: runtimeSort },
      dataflowId || null,
      "runtime",
      asOf,
      "all",
      registryPresentationScope,
      registryGeneration,
    ),
    enabled: Boolean(dataflowId),
  });

  const committed = useRef<RegistryComposition>({
    scope: compositionScope,
    origin: null,
    overview: null,
    sections: Array.from({ length: 5 }),
  });
  if (committed.current.scope !== compositionScope) {
    committed.current = {
      scope: compositionScope,
      origin: null,
      overview: null,
      sections: Array.from({ length: 5 }),
    };
  }

  const pageScopes = [
    `${executionLimit}:${executionOffset}:${JSON.stringify(executionSort)}`,
    `${runtimeLimit}:${runtimeOffset}:${JSON.stringify(runtimeSort)}`,
  ];
  const previousPageScopes = useRef(pageScopes);
  pageScopes.forEach((scope, index) => {
    if (scope !== previousPageScopes.current[index]) committed.current.sections[index + 3] = undefined;
  });
  previousPageScopes.current = pageScopes;

  const queries = [sectionQuery, configurationQuery, technicalQuery, executionQuery, runtimeQuery];
  const parentOriginForEarlySections = isMonitoringRegistryOriginValid(parentOrigin)
    && parentOrigin.environmentId === environmentId
    && parentOrigin.dataflowId === dataflowId
    && parentOrigin.scope === filterScope
    && parentOrigin.presentationScope === registryPresentationScope
    && sameMonitoringRegistryInstant(parentOrigin.asOf, asOf)
    ? parentOrigin
    : null;
  const responseOrigin = (data: RegistryResponse, seed?: MonitoringRegistryOrigin | null) => monitoringRegistryOriginForResponse(
    environmentId,
    seed?.facet ?? parentOrigin?.facet ?? "all",
    filters,
    data.records[0] ?? row,
    data,
    registryPresentationScope,
  );
  const usableResponse = (data: RegistryResponse | undefined, query: { isPlaceholderData: boolean }, origin: MonitoringRegistryOrigin | null) => Boolean(
    data
      && !query.isPlaceholderData
      && monitoringRegistryResponseMatchesOrigin(data, origin),
  );

  const detailResponseMatchesRequestedAsOf = Boolean(
    detailQuery.data
      && sameMonitoringRegistryInstant(detailQuery.data.summary.as_of, asOf),
  );
  const requestedAsOfRefreshKey = useRef("");
  useEffect(() => {
    if (!detailQuery.data || detailQuery.isPlaceholderData || detailResponseMatchesRequestedAsOf || detailQuery.isFetching) return;
    if (requestedAsOfRefreshKey.current === asOf) return;
    requestedAsOfRefreshKey.current = asOf;
    void detailQuery.refetch();
  }, [asOf, detailQuery.data, detailQuery.isFetching, detailQuery.isPlaceholderData, detailQuery.refetch, detailResponseMatchesRequestedAsOf]);
  const detailCandidate = detailQuery.data && !detailQuery.isPlaceholderData && detailResponseMatchesRequestedAsOf
    ? responseOrigin(detailQuery.data, parentOrigin)
    : null;
  const detailCandidateValid = isMonitoringRegistryOriginValid(detailCandidate)
    && (!parentOriginForEarlySections || monitoringRegistryOriginsMatch(detailCandidate, parentOriginForEarlySections));
  const parentOriginMismatch = Boolean(
    parentOriginForEarlySections
      && detailCandidate
      && !monitoringRegistryOriginsMatch(detailCandidate, parentOriginForEarlySections),
  );
  const parentOriginRefreshKey = parentOriginMismatch && parentOriginForEarlySections && detailCandidate
    ? JSON.stringify([
      parentOriginForEarlySections.environmentId,
      parentOriginForEarlySections.facet,
      parentOriginForEarlySections.dataflowId,
      parentOriginForEarlySections.scope,
      parentOriginForEarlySections.asOf,
      parentOriginForEarlySections.analyticsGeneration,
      parentOriginForEarlySections.identityVersion,
      parentOriginForEarlySections.presentationScope,
      detailCandidate?.asOf,
      detailCandidate?.analyticsGeneration,
      detailCandidate?.identityVersion,
    ])
    : "";
  const requestedParentRefreshKey = useRef("");
  useEffect(() => {
    if (!parentOriginRefreshKey) {
      requestedParentRefreshKey.current = "";
      return;
    }
    if (detailQuery.isFetching || requestedParentRefreshKey.current === parentOriginRefreshKey) return;
    requestedParentRefreshKey.current = parentOriginRefreshKey;
    void queryClient.invalidateQueries({
      queryKey: [...environmentQueryKeys.monitoring(environmentId), "dataflow-registry", "list"],
      refetchType: "active",
    }).catch(() => undefined);
  }, [detailQuery.isFetching, environmentId, parentOriginRefreshKey, queryClient]);
  if (detailQuery.data && detailCandidate && detailCandidateValid) {
    const previousOrigin = committed.current.origin;
    const originChanged = !previousOrigin
      || previousOrigin.scope !== detailCandidate.scope
      || previousOrigin.presentationScope !== detailCandidate.presentationScope
      || previousOrigin.analyticsGeneration !== detailCandidate.analyticsGeneration
      || previousOrigin.identityVersion !== detailCandidate.identityVersion
      || !sameMonitoringRegistryInstant(previousOrigin.asOf, detailCandidate.asOf);
    committed.current.origin = detailCandidate;
    committed.current.overview = detailQuery.data;
    if (originChanged) {
      committed.current.sections = queries.map((query) => usableResponse(query.data, query, detailCandidate) ? query.data : undefined);
    }
  } else if (!committed.current.overview && parentOriginForEarlySections) {
    committed.current.origin = parentOriginForEarlySections;
    for (const index of [3, 4]) {
      const query = queries[index];
      if (usableResponse(query.data, query, parentOriginForEarlySections)) committed.current.sections[index] = query.data;
    }
  }

  if (committed.current.overview && committed.current.origin) {
    queries.forEach((query, index) => {
      if (usableResponse(query.data, query, committed.current.origin)) committed.current.sections[index] = query.data;
    });
  }

  const composition = committed.current;
  const detailData = composition.overview;
  const [sectionData, configurationData, technicalData, executionData, runtimeData] = composition.sections;
  const detailGeneration = composition.origin?.analyticsGeneration ?? null;
  const sectionEnabled = [
    openSections.has("read-watermark"),
    openSections.has("configuration"),
    openSections.has("technical"),
    true,
    true,
  ];
  const sectionRefreshScope = JSON.stringify([
    compositionScope,
    composition.origin?.scope ?? null,
    composition.origin?.asOf ?? null,
    composition.origin?.analyticsGeneration ?? null,
    composition.origin?.identityVersion ?? null,
    registryGeneration,
    pageScopes,
  ]);
  const [sectionRecoveryErrors, setSectionRecoveryErrors] = useState<Record<string, string>>({});
  const sectionRefreshState = useRef<{ scope: string; keys: Set<number>; overviewRequested: boolean }>({ scope: sectionRefreshScope, keys: new Set(), overviewRequested: false });
  if (sectionRefreshState.current.scope !== sectionRefreshScope) {
    sectionRefreshState.current = { scope: sectionRefreshScope, keys: new Set(), overviewRequested: false };
  }
  useEffect(() => {
    setSectionRecoveryErrors((current) => {
      const prefix = `${sectionRefreshScope}:`;
      const next = Object.fromEntries(Object.entries(current).filter(([key]) => key.startsWith(prefix)));
      return Object.keys(next).length === Object.keys(current).length ? current : next;
    });
  }, [sectionRefreshScope]);
  useEffect(() => {
    if (!isMonitoringRegistryOriginValid(composition.origin)) return;
    queries.forEach((query, index) => {
      if (!sectionEnabled[index] || !query.data || query.isPlaceholderData || query.isFetching) return;
      const recoveryKey = `${sectionRefreshScope}:${index}`;
      if (monitoringRegistryResponseMatchesOrigin(query.data, composition.origin)) {
        setSectionRecoveryErrors((current) => {
          if (!current[recoveryKey]) return current;
          const next = { ...current };
          delete next[recoveryKey];
          return next;
        });
        return;
      }
      const expectedOrigin = composition.origin;
      if (!sectionRefreshState.current.keys.has(index)) {
        sectionRefreshState.current.keys.add(index);
        void query.refetch().then((result) => {
          if (sectionRefreshState.current.scope !== sectionRefreshScope) return;
          if (monitoringRegistryResponseMatchesOrigin(result.data, expectedOrigin)) {
            setSectionRecoveryErrors((current) => {
              if (!current[recoveryKey]) return current;
              const next = { ...current };
              delete next[recoveryKey];
              return next;
            });
            return;
          }
          setSectionRecoveryErrors((current) => ({
            ...current,
            [recoveryKey]: "This section is still on a different snapshot. Retry after the profile refresh completes.",
          }));
        }).catch(() => {
          if (sectionRefreshState.current.scope !== sectionRefreshScope) return;
          setSectionRecoveryErrors((current) => ({
            ...current,
            [recoveryKey]: "Unable to refresh this section for the current snapshot.",
          }));
        });
      }
      if (!sectionRefreshState.current.overviewRequested && !detailQuery.isFetching) {
        sectionRefreshState.current.overviewRequested = true;
        void detailQuery.refetch().catch(() => undefined);
      }
    });
  }, [composition.origin, detailQuery.isFetching, detailQuery.refetch, executionQuery.data, executionQuery.isFetching, executionQuery.isPlaceholderData, openSections, queries, runtimeQuery.data, runtimeQuery.isFetching, runtimeQuery.isPlaceholderData, sectionData, sectionEnabled, sectionRefreshScope, configurationData, technicalData]);
  const sectionRows = {
    "read-watermark": matchingSectionRow(sectionData, composition.origin),
    configuration: matchingSectionRow(configurationData, composition.origin),
    technical: matchingSectionRow(technicalData, composition.origin),
  };
  const sectionResponseReady = {
    "read-watermark": monitoringRegistryResponseMatchesOrigin(sectionData, composition.origin),
    configuration: monitoringRegistryResponseMatchesOrigin(configurationData, composition.origin),
    technical: monitoringRegistryResponseMatchesOrigin(technicalData, composition.origin),
  };
  const executionMatches = monitoringRegistryResponseMatchesOrigin(executionData, composition.origin);
  const runtimeMatches = monitoringRegistryResponseMatchesOrigin(runtimeData, composition.origin);
  const retryRegistrySection = (index: number, query: { refetch: () => Promise<{ data?: RegistryResponse }> }) => {
    const recoveryKey = `${sectionRefreshScope}:${index}`;
    const expectedOrigin = composition.origin;
    sectionRefreshState.current.keys.add(index);
    setSectionRecoveryErrors((current) => {
      if (!current[recoveryKey]) return current;
      const next = { ...current };
      delete next[recoveryKey];
      return next;
    });
    void query.refetch().then((result) => {
      if (sectionRefreshState.current.scope !== sectionRefreshScope) return;
      if (monitoringRegistryResponseMatchesOrigin(result.data, expectedOrigin)) return;
      setSectionRecoveryErrors((current) => ({
        ...current,
        [recoveryKey]: "This section is still on a different snapshot. Retry after the profile refresh completes.",
      }));
      void detailQuery.refetch().catch(() => undefined);
    }).catch(() => {
      if (sectionRefreshState.current.scope !== sectionRefreshScope) return;
      setSectionRecoveryErrors((current) => ({
        ...current,
        [recoveryKey]: "Unable to refresh this section for the current snapshot.",
      }));
    });
  };
  const overviewState = {
    loading: detailQuery.isFetching || (detailQuery.isPending && !detailQuery.isError) || Boolean(detailQuery.data && !detailQuery.isPlaceholderData && !detailResponseMatchesRequestedAsOf),
    ready: Boolean(detailData?.records[0]) && isMonitoringRegistryOriginValid(composition.origin),
    preview: !detailData && !detailQuery.isError && Boolean(row.dataflow_id),
    error: detailQuery.isError ? (detailQuery.error instanceof Error ? detailQuery.error.message : "Unable to load this dataflow profile") : null,
    retry: () => { void detailQuery.refetch(); },
  };
  const runtimeRecoveryError = sectionRecoveryErrors[`${sectionRefreshScope}:4`] ?? null;
  const executionRecoveryError = sectionRecoveryErrors[`${sectionRefreshScope}:3`] ?? null;
  const onSectionOpen = (section: RegistrySection) => {
    setOpenSections((current) => current.has(section) ? current : new Set([...current, section]));
  };

  return (
    <MonitoringDetailDrawer
      {...drawerProps}
      key={`registry:${environmentId}:${dataflowId}:${compositionScope}`}
      kind="dataflow-registry"
      row={detailData?.records[0] ?? row}
      environmentId={environmentId}
      registryOverviewState={overviewState}
      onOpenRegistrySection={onSectionOpen}
      registrySectionStates={{
        "read-watermark": sectionState(sectionQuery, sectionResponseReady["read-watermark"], sectionResponseReady["read-watermark"] && !sectionRows["read-watermark"], sectionQuery.isFetching, sectionRecoveryErrors[`${sectionRefreshScope}:0`], () => retryRegistrySection(0, sectionQuery)),
        configuration: sectionState(configurationQuery, sectionResponseReady.configuration, sectionResponseReady.configuration && !sectionRows.configuration, configurationQuery.isFetching, sectionRecoveryErrors[`${sectionRefreshScope}:1`], () => retryRegistrySection(1, configurationQuery)),
        technical: sectionState(technicalQuery, sectionResponseReady.technical, sectionResponseReady.technical && !sectionRows.technical, technicalQuery.isFetching, sectionRecoveryErrors[`${sectionRefreshScope}:2`], () => retryRegistrySection(2, technicalQuery)),
      }}
      registrySectionRows={sectionRows}
      registryAnalyticsGeneration={detailGeneration}
      registryOrigin={composition.origin}
      registryRuntimeRows={runtimeMatches ? runtimeData?.records ?? [] : []}
      registryRuntimeTotal={runtimeMatches ? runtimeData?.summary.total_records ?? runtimeData?.records.length ?? 0 : 0}
      registryRuntimeOffset={runtimeOffset}
      registryRuntimeLimit={runtimeLimit}
      registryRuntimeLoading={runtimeQuery.isPending || runtimeQuery.isFetching || (!runtimeMatches && !runtimeRecoveryError)}
      registryRuntimeError={runtimeRecoveryError ?? (runtimeQuery.isError ? (runtimeQuery.error instanceof Error ? runtimeQuery.error.message : "Unable to load runtime combinations") : null)}
      onRetryRegistryRuntime={() => retryRegistrySection(4, runtimeQuery)}
      registryRuntimeSort={runtimeSort}
      onRegistryRuntimePageChange={setRuntimeOffset}
      onRegistryRuntimePageSizeChange={(limit) => {
        setRuntimeLimit(limit);
        setRuntimeOffset(0);
      }}
      onRegistryRuntimeSort={(sort) => {
        setRuntimeSort(sort);
        setRuntimeOffset(0);
      }}
      registryExecutionRows={executionMatches ? executionData?.records as MonitoringRecord[] | undefined : []}
      registryExecutionTotal={executionMatches ? executionData?.summary.total_records ?? executionData?.records.length ?? 0 : 0}
      registryExecutionOffset={executionOffset}
      registryExecutionLimit={executionLimit}
      onRegistryExecutionPageChange={setExecutionOffset}
      onRegistryExecutionPageSizeChange={(limit) => {
        setExecutionLimit(limit);
        setExecutionOffset(0);
      }}
      registryExecutionLoading={executionQuery.isPending || executionQuery.isFetching || (!executionMatches && !executionRecoveryError)}
      registryExecutionError={executionRecoveryError ?? (executionQuery.isError ? (executionQuery.error instanceof Error ? executionQuery.error.message : "Unable to load execution history") : null)}
      onRetryRegistryExecution={() => retryRegistrySection(3, executionQuery)}
      registryExecutionSort={executionSort}
      onRegistryExecutionSort={(sort) => {
        setExecutionSort(sort);
        setExecutionOffset(0);
      }}
      onOpenRegistryRun={drawerProps.onOpenDataflow}
      onOpenDataflow={drawerProps.onOpenDataflow}
    />
  );
}

function matchingSectionRow(data: RegistryResponse | undefined, origin: MonitoringRegistryOrigin | null) {
  return monitoringRegistryResponseMatchesOrigin(data, origin)
    ? data?.records[0] as Record<string, unknown> | undefined
    : undefined;
}

function sectionState(query: {
  isPending: boolean;
  isFetching: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => Promise<unknown>;
}, ready = false, empty = false, refreshing = false, recoveryError?: string, onRetry?: () => void) {
  return {
    ready,
    empty,
    loading: !recoveryError && (!ready || refreshing || query.isPending || query.isFetching),
    error: recoveryError ?? (query.isError ? (query.error instanceof Error ? query.error.message : "Unable to load this section") : null),
    retry: onRetry ?? (() => { void query.refetch(); }),
  };
}
