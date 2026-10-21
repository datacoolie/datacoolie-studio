import { Fragment, useEffect, useId, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  DiagnosticsFindingGroup,
  DiagnosticsFindingObject,
  DiagnosticsFindingsSummary,
  DiagnosticsScopeNotice,
  MonitoringReport,
} from "../../../shared/api/domainTypes";
import type { TableSort } from "../../../shared/components/DataTable";
import { Help } from "../../../shared/components/Help";
import { ApiRequestError } from "../../../shared/lib/errors";
import { environmentQueryKeys } from "../../environments/environmentQueries";
import type { MonitoringFilters } from "../monitoringFilters";
import { monitoringDiagnosticsEvidenceOptions, monitoringQueryParams } from "../monitoringQueries";
import { CompactNumberValue, DataTable, DetailMetric, ReportPanel, TablePager, formatTimestampForDisplay, type MetricValue } from "../components/monitoringPresentation";
import { diagnosticsSourceLabel, firstMetric, numberValue } from "../diagnosticsPresentation";
import {
  findingGroupSummary,
  findingCountLabel,
  findingKindPresentation,
  findingObjectIssueText,
  findingObjectToDrawerRow,
  findingReasonText,
  findingRuleTitle,
  findingSectionLabel,
  findingUnits,
  scopeNoticeText,
} from "../diagnosticsFindings";

type DiagnosticsRow = Record<string, unknown>;
type RegistryTab = "findings" | "limitations" | "capture";
const DIAGNOSTICS_DEFAULT_PAGE_SIZE = 50;
const DIAGNOSTIC_CAPTURE_PAGE_SIZE = DIAGNOSTICS_DEFAULT_PAGE_SIZE;
const TAB_ORDER: RegistryTab[] = ["findings", "limitations", "capture"];

export function DiagnosticsRegistry({ report, environmentId, filters, timezoneName, onInspect }: {
  report: MonitoringReport;
  environmentId: number;
  filters: MonitoringFilters;
  timezoneName: string | null;
  onInspect?: (row: DiagnosticsRow) => void;
}) {
  const diagnostics = (report.diagnostics ?? {}) as unknown as DiagnosticsRow;
  const summary = (diagnostics.findings_summary ?? null) as DiagnosticsFindingsSummary | null;
  const notices = (Array.isArray(diagnostics.scope_notices) ? diagnostics.scope_notices : []) as DiagnosticsScopeNotice[];
  const reportGroups = (Array.isArray(diagnostics.finding_groups) ? diagnostics.finding_groups : []) as DiagnosticsFindingGroup[];
  const capture = asRecord(diagnostics.diagnostic_capture);
  const captureSummary = asRecord(capture?.summary);
  const [tab, setTab] = useState<RegistryTab>("findings");
  const [groupLimit, setGroupLimit] = useState(DIAGNOSTICS_DEFAULT_PAGE_SIZE);
  const [groupOffset, setGroupOffset] = useState(0);
  const [groupSort, setGroupSort] = useState<TableSort | null>(null);
  const [selected, setSelected] = useState<DiagnosticsFindingGroup | null>(null);
  const [objectLimit, setObjectLimit] = useState(DIAGNOSTICS_DEFAULT_PAGE_SIZE);
  const [objectOffset, setObjectOffset] = useState(0);
  const [objectSort, setObjectSort] = useState<TableSort | null>(null);
  const [captureOffset, setCaptureOffset] = useState(0);
  const [captureLimit, setCaptureLimit] = useState(DIAGNOSTIC_CAPTURE_PAGE_SIZE);
  const id = useId();
  const queryClient = useQueryClient();
  const asOf = summary?.as_of ?? null;
  const generation = summary?.analytics_generation ?? null;
  // A new report scope or analytics generation restarts both pagers; rows from
  // two snapshots are never combined.
  const scopeKey = useMemo(
    () => JSON.stringify([environmentId, monitoringQueryParams(filters), asOf, generation]),
    [environmentId, filters, asOf, generation],
  );
  useEffect(() => {
    setGroupOffset(0); setObjectOffset(0); setCaptureOffset(0); setSelected(null);
  }, [scopeKey]);
  // Issues/warnings and limitations are different populations.
  useEffect(() => { setGroupOffset(0); setGroupSort(null); }, [tab]);

  const groupKind = tab === "limitations" ? "limitations" : "findings";
  const groupsQuery = useQuery(monitoringDiagnosticsEvidenceOptions(environmentId, filters, {
    view: "registry", findingKind: groupKind, limit: groupLimit, offset: groupOffset, sort: groupSort,
    asOf, generation, enabled: Boolean(summary) && tab !== "capture" && selected === null,
  }));
  const objectsQuery = useQuery(monitoringDiagnosticsEvidenceOptions(environmentId, filters, {
    view: "detail", findingGroup: selected?.group_key ?? null, limit: objectLimit, offset: objectOffset, sort: objectSort,
    asOf, generation, enabled: Boolean(summary) && selected !== null,
  }));
  const activeError = (selected ? objectsQuery.error : groupsQuery.error) as unknown;
  const generationChanged = activeError instanceof ApiRequestError && activeError.status === 409;
  useEffect(() => {
    if (generationChanged) void queryClient.invalidateQueries({ queryKey: environmentQueryKeys.monitoring(environmentId) });
  }, [generationChanged, queryClient, environmentId]);

  const findingsTotal = numberValue(summary?.issue_groups) + numberValue(summary?.warning_groups);
  const limitationsTotal = numberValue(summary?.limitation_groups);
  const captureTotal = firstMetric(captureSummary, "known_markers") ?? 0;
  const isFirstDefaultPage = groupOffset === 0 && groupSort === null && groupLimit === DIAGNOSTICS_DEFAULT_PAGE_SIZE;
  const fallbackGroups = isFirstDefaultPage
    ? reportGroups.filter((group) => (tab === "limitations" ? group.kind === "limitation" : group.kind !== "limitation"))
    : [];
  const groups = groupsQuery.data?.groups ?? fallbackGroups;
  const groupTotal = groupsQuery.data?.summary.total_records ?? (tab === "limitations" ? limitationsTotal : findingsTotal);
  const objects = objectsQuery.data?.objects ?? [];
  const objectTotal = objectsQuery.data?.summary.total_records ?? numberValue(selected?.affected_objects);
  const objectsFresh = objectsQuery.data?.mode === "detail" && objectsQuery.data.group?.group_key === selected?.group_key;
  const noEvidence = !summary || summary.scope === "none";

  const openGroup = (group: DiagnosticsFindingGroup) => { setSelected(group); setObjectOffset(0); setObjectSort(null); };
  const openNotice = (notice: DiagnosticsScopeNotice) => openGroup({
    group_key: notice.group_key, kind: "notice", rule: "scope_child_subset", reason: notice.code, section: "",
    subject_kind: "job", affected_objects: notice.jobs, checks: notice.checks,
  });
  const inspectObject = (object: DiagnosticsFindingObject) =>
    onInspect?.(findingObjectToDrawerRow(object, selected?.kind ?? "issue"));

  const captureRows = arrayRows(capture?.evidence);
  const pager = selected
    ? <TablePager limit={objectLimit} offset={objectOffset} loadedRows={objects.length} totalRows={objectTotal} loading={objectsQuery.isFetching}
        onPageChange={setObjectOffset} onPageSizeChange={(next) => { setObjectLimit(next); setObjectOffset(0); }} />
    : tab === "capture"
      ? <TablePager limit={captureLimit} offset={captureOffset} loadedRows={Math.min(captureLimit, Math.max(0, captureRows.length - captureOffset))} totalRows={captureRows.length} loading={false}
          onPageChange={setCaptureOffset} onPageSizeChange={(next) => { setCaptureLimit(next); setCaptureOffset(0); }} />
      : <TablePager limit={groupLimit} offset={groupOffset} loadedRows={groups.length} totalRows={groupTotal} loading={groupsQuery.isFetching}
          onPageChange={setGroupOffset} onPageSizeChange={(next) => { setGroupLimit(next); setGroupOffset(0); }} />;

  const tabs = (
    <div className="diagnostics-registry-tabs" role="tablist" aria-label="Diagnostic findings" onKeyDown={event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const index = TAB_ORDER.indexOf(tab);
      const next = event.key === "Home" ? TAB_ORDER[0] : event.key === "End" ? TAB_ORDER[TAB_ORDER.length - 1]
        : TAB_ORDER[(index + (event.key === "ArrowRight" ? 1 : TAB_ORDER.length - 1)) % TAB_ORDER.length];
      setTab(next); setSelected(null); document.getElementById(`${id}-${next}-tab`)?.focus();
    }}>
      <button type="button" role="tab" id={`${id}-findings-tab`} aria-controls={`${id}-findings-panel`} aria-selected={tab === "findings"} tabIndex={tab === "findings" ? 0 : -1} onClick={() => { setTab("findings"); setSelected(null); }}>Issues and warnings <CompactNumberValue value={findingsTotal} /></button>
      <button type="button" role="tab" id={`${id}-limitations-tab`} aria-controls={`${id}-limitations-panel`} aria-selected={tab === "limitations"} tabIndex={tab === "limitations" ? 0 : -1} onClick={() => { setTab("limitations"); setSelected(null); }}>Limitations <CompactNumberValue value={limitationsTotal} /></button>
      <button type="button" role="tab" id={`${id}-capture-tab`} aria-controls={`${id}-capture-panel`} aria-selected={tab === "capture"} tabIndex={tab === "capture" ? 0 : -1} onClick={() => { setTab("capture"); setSelected(null); }}>Captured detail <CompactNumberValue value={captureTotal as MetricValue} fallback="—" /></button>
    </div>
  );

  return <ReportPanel title="Diagnostic findings" className="monitoring-diagnostics-registry-panel"
    titleTooltip="Grouped findings for the selected evidence. Counts use the unit shown on each row (jobs, dataflow records, field groups) and are never added across units. Select a group to list every affected object."
    headerAction={tabs}>
    {notices.length ? <div className="diagnostics-scope-notices">
      {notices.map((notice) => <div key={notice.group_key} className="diagnostics-scope-notice" role="note">
        <span>{scopeNoticeText(notice)}</span>
        <button type="button" className="text-action" onClick={() => { setTab("limitations"); openNotice(notice); }}>View jobs</button>
      </div>)}
    </div> : null}
    <div className="diagnostics-registry-toolbar">
      {tab === "capture" && summary && summary.scope !== "none"
        ? <div className="diagnostics-toolbar-caption"><CaptureSummaryLine capture={capture} /></div>
        : <Help as="small" className="monitoring-ellipsis" title={findingsCaption(summary, tab)}>{findingsCaption(summary, tab)}</Help>}
      {pager}
    </div>
    <div id={`${id}-findings-panel`} role="tabpanel" aria-labelledby={`${id}-findings-tab`} hidden={tab !== "findings"} tabIndex={0} className="diagnostics-registry-tab-panel">
      {tab === "findings" ? <FindingsPanel noEvidence={noEvidence} emptyText={limitationsTotal ? `No confirmed issues or warnings. ${limitationsTotal} assessment limitation group${limitationsTotal === 1 ? "" : "s"} available.` : "No confirmed issues or warnings in the selected evidence."}
        selected={selected} groups={groups} objects={objectsFresh ? objects : []} loading={selected ? objectsQuery.isFetching && !objectsFresh : groupsQuery.isLoading}
        error={generationChanged ? "The analytics snapshot changed. Refreshing the report…" : activeError ? "Findings could not be loaded." : null}
        onRetry={() => { void (selected ? objectsQuery.refetch() : groupsQuery.refetch()); }}
        groupOffset={groupOffset} objectOffset={objectOffset} groupSort={groupSort} objectSort={objectSort}
        onGroupSort={(sort) => { setGroupSort(sort); setGroupOffset(0); }} onObjectSort={(sort) => { setObjectSort(sort); setObjectOffset(0); }}
        onOpenGroup={openGroup} onBack={() => setSelected(null)} onInspectObject={inspectObject} timezoneName={timezoneName} /> : null}
    </div>
    <div id={`${id}-limitations-panel`} role="tabpanel" aria-labelledby={`${id}-limitations-tab`} hidden={tab !== "limitations"} tabIndex={0} className="diagnostics-registry-tab-panel">
      {tab === "limitations" ? <FindingsPanel noEvidence={noEvidence} emptyText="No assessment limitations in the selected evidence."
        selected={selected} groups={groups} objects={objectsFresh ? objects : []} loading={selected ? objectsQuery.isFetching && !objectsFresh : groupsQuery.isLoading}
        error={generationChanged ? "The analytics snapshot changed. Refreshing the report…" : activeError ? "Limitations could not be loaded." : null}
        onRetry={() => { void (selected ? objectsQuery.refetch() : groupsQuery.refetch()); }}
        groupOffset={groupOffset} objectOffset={objectOffset} groupSort={groupSort} objectSort={objectSort}
        onGroupSort={(sort) => { setGroupSort(sort); setGroupOffset(0); }} onObjectSort={(sort) => { setObjectSort(sort); setObjectOffset(0); }}
        onOpenGroup={openGroup} onBack={() => setSelected(null)} onInspectObject={inspectObject} timezoneName={timezoneName} /> : null}
    </div>
    <div id={`${id}-capture-panel`} role="tabpanel" aria-labelledby={`${id}-capture-tab`} hidden={tab !== "capture"} tabIndex={0} className="diagnostics-registry-tab-panel">
      <DiagnosticCapturePanel capture={capture} evidenceOffset={captureOffset} evidenceLimit={captureLimit} timezoneName={timezoneName} onInspect={onInspect} />
    </div>
  </ReportPanel>;
}

function findingsCaption(summary: DiagnosticsFindingsSummary | null, tab: RegistryTab) {
  if (!summary || summary.scope === "none") return "No evidence in the selected scope";
  if (tab === "capture") return "Current filters";
  const byKind = new Map<string, string[]>();
  for (const item of summary.affected_objects.filter((entry) => (tab === "limitations" ? entry.kind === "limitation" : entry.kind !== "limitation"))) {
    const label = item.kind === "issue" ? "issues" : item.kind === "warning" ? "warnings" : "limitations";
    byKind.set(label, [...(byKind.get(label) ?? []), `${numberValue(item.count)} ${findingUnits({ subject_kind: item.subject_kind, rule: "" }).objects}`]);
  }
  const parts = [...byKind].map(([label, counts]) => `${label} ${counts.join(", ")}`);
  return `Current filters · ${parts.length ? parts.join(" · ") : "no affected objects"}`;
}

/** Capture counts keep distinct units: dataflow records, markers, and markers per reason. */
export function CaptureSummaryLine({ capture }: { capture: DiagnosticsRow | null }) {
  const summary = asRecord(capture?.summary);
  if (!summary) return <span>Current filters · no captured detail summary</span>;
  const affected = firstMetric(summary, "affected_records", "affected_dataflow_records");
  const markerCount = firstMetric(summary, "known_markers", "diagnostic_markers");
  const omittedRecords = firstMetric(summary, "omitted_marker_records", "omitted_markers");
  const reasons = arrayRows(capture?.by_reason)
    .map((row) => ({ reason: String(row.reason ?? "unknown"), count: row.count as MetricValue }))
    .sort((left, right) => numberValue(right.count) - numberValue(left.count) || left.reason.localeCompare(right.reason));
  const full = [
    "Current filters",
    `${numberValue(affected)} affected dataflow records`,
    `${numberValue(markerCount)} markers`,
    ...reasons.map((item) => `${numberValue(item.count)} ${item.reason}`),
    `${numberValue(omittedRecords)} omitted marker-list records`,
  ].join(" · ");
  return (
    <Help className="diagnostics-capture-summary" title={full}>
      <span>Current filters</span>
      <span className="separator">·</span>
      <DetailMetric label="records" value={<CompactNumberValue value={affected as MetricValue} fallback="Unknown" />} tone={numberValue(affected) > 0 ? "warning" : "neutral"} labelFirst />
      <span className="separator">·</span>
      <DetailMetric label="markers" value={<CompactNumberValue value={markerCount as MetricValue} fallback="Unknown" />} tone="neutral" labelFirst />
      {reasons.map((item) => (
        <Fragment key={item.reason}>
          <span className="separator">·</span>
          <DetailMetric label={item.reason} value={<CompactNumberValue value={item.count} />} tone={item.reason === "omitted" && numberValue(item.count) > 0 ? "warning" : "neutral"} labelFirst />
        </Fragment>
      ))}
    </Help>
  );
}

function FindingsPanel({ noEvidence, emptyText, selected, groups, objects, loading, error, onRetry, groupOffset, objectOffset, groupSort, objectSort, onGroupSort, onObjectSort, onOpenGroup, onBack, onInspectObject, timezoneName }: {
  noEvidence: boolean;
  emptyText: string;
  selected: DiagnosticsFindingGroup | null;
  groups: DiagnosticsFindingGroup[];
  objects: DiagnosticsFindingObject[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  groupOffset: number;
  objectOffset: number;
  groupSort: TableSort | null;
  objectSort: TableSort | null;
  onGroupSort: (sort: TableSort) => void;
  onObjectSort: (sort: TableSort) => void;
  onOpenGroup: (group: DiagnosticsFindingGroup) => void;
  onBack: () => void;
  onInspectObject: (object: DiagnosticsFindingObject) => void;
  timezoneName: string | null;
}) {
  if (error) return <div className="table-empty" role="alert">{error} <button type="button" className="text-action" onClick={onRetry}>Retry</button></div>;
  if (selected) {
    const kind = findingKindPresentation(selected.kind);
    const units = findingUnits(selected);
    return <div className="diagnostics-group-objects">
      <div className="diagnostics-group-header">
        <button type="button" className="text-action" onClick={onBack}>← All groups</button>
        <strong>{findingRuleTitle(selected.rule)}</strong>
        <span className={`diagnostics-severity diagnostics-${kind.tone}`}>{kind.label}</span>
        <small>{findingGroupSummary(selected)} · {findingCountLabel(numberValue(selected.affected_objects), units.objects)}{selected.checks ? ` · ${numberValue(selected.checks)} checks` : ""}{selected.occurrences && units.detail ? ` · ${numberValue(selected.occurrences)} ${units.detail}` : ""}</small>
      </div>
      {loading && !objects.length ? <div className="table-empty">Loading affected objects…</div> : !objects.length ? <div className="table-empty">No affected objects remain for this group in the current snapshot.</div> : <FindingObjectsTable rows={objects} offset={objectOffset} sort={objectSort} onSort={onObjectSort} timezoneName={timezoneName} onInspect={onInspectObject} />}
    </div>;
  }
  if (!groups.length) return <div className="table-empty">{loading ? "Loading findings…" : noEvidence ? "No evidence in the selected scope." : emptyText}</div>;
  return <FindingGroupsTable rows={groups} offset={groupOffset} sort={groupSort} onSort={onGroupSort} timezoneName={timezoneName} onOpen={onOpenGroup} />;
}

function FindingGroupsTable({ rows, offset, sort, onSort, timezoneName, onOpen }: {
  rows: DiagnosticsFindingGroup[];
  offset: number;
  sort: TableSort | null;
  onSort: (sort: TableSort) => void;
  timezoneName: string | null;
  onOpen: (group: DiagnosticsFindingGroup) => void;
}) {
  return <DataTable
    rows={rows as unknown as DiagnosticsRow[]}
    compactNumbers
    compactNumberStyle="infix"
    sortMode="server"
    sort={sort ?? { sortBy: "priority", sortDir: "asc" }}
    onSort={onSort}
    getRowKey={(row) => String(row.group_key)}
    columns={[
      { key: "kind", label: "Type", sortable: true, autoFit: true, render: (row) => <KindCell kind={(row as unknown as DiagnosticsFindingGroup).kind} />, measureValue: (row) => findingKindPresentation((row as unknown as DiagnosticsFindingGroup).kind).label, measureSelectors: [".diagnostics-severity"] },
      { key: "rule", label: "Finding", sortable: true, minWidth: 220, fillPriority: "last", render: (row) => <GroupFindingCell group={row as unknown as DiagnosticsFindingGroup} /> },
      { key: "section", label: "Scope", sortable: true, autoFit: true, minWidth: 120, maxWidth: 240, render: (row) => <GroupScopeCell group={row as unknown as DiagnosticsFindingGroup} /> },
      { key: "affected_objects", label: "Affected", sortable: true, autoFit: true, minWidth: 150, maxWidth: 220, render: (row) => <GroupAffectedCell group={row as unknown as DiagnosticsFindingGroup} /> },
      { key: "latest_time", label: "Latest", sortable: true, autoFit: true, minWidth: 148, maxWidth: 184, render: (row) => formatTime(row.latest_time, timezoneName) },
    ]}
    maxRows={Math.max(1, rows.length)}
    offset={offset}
    timezoneName={timezoneName}
    onRowClick={(row) => onOpen(row as unknown as DiagnosticsFindingGroup)}
    className="diagnostics-queue-table monitoring-table-one-line"
  />;
}

function KindCell({ kind }: { kind: DiagnosticsFindingGroup["kind"] }) {
  const presentation = findingKindPresentation(kind);
  return <span className={`diagnostics-severity diagnostics-${presentation.tone}`}>{presentation.label}</span>;
}

function GroupFindingCell({ group }: { group: DiagnosticsFindingGroup }) {
  const title = findingRuleTitle(group.rule);
  const reason = findingReasonText(group);
  return <span className="monitoring-stack-cell diagnostics-object-cell"><Help as="strong" className="monitoring-ellipsis" title={title}>{title}</Help><Help as="small" className="monitoring-ellipsis" title={reason}>{reason}</Help></span>;
}

function GroupScopeCell({ group }: { group: DiagnosticsFindingGroup }) {
  const label = findingSectionLabel(group.rule, group.section);
  return label ? <Help className="monitoring-ellipsis" title={label}>{label}</Help> : <span className="diagnostics-muted">Job evidence</span>;
}

function GroupAffectedCell({ group }: { group: DiagnosticsFindingGroup }) {
  const units = findingUnits(group);
  const detail = group.checks ? `${numberValue(group.checks)} checks` : group.occurrences && units.detail ? `${numberValue(group.occurrences)} ${units.detail}` : null;
  return <span className="monitoring-stack-cell diagnostics-object-cell"><strong className="monitoring-ellipsis">{findingCountLabel(numberValue(group.affected_objects), units.objects)}</strong>{detail ? <small className="monitoring-ellipsis">{detail}</small> : null}</span>;
}

function FindingObjectsTable({ rows, offset, sort, onSort, timezoneName, onInspect }: {
  rows: DiagnosticsFindingObject[];
  offset: number;
  sort: TableSort | null;
  onSort: (sort: TableSort) => void;
  timezoneName: string | null;
  onInspect: (object: DiagnosticsFindingObject) => void;
}) {
  const capture = rows[0]?.rule === "diagnostic_capture";
  return <DataTable
    rows={rows as unknown as DiagnosticsRow[]}
    compactNumbers
    compactNumberStyle="infix"
    sortMode="server"
    sort={sort ?? { sortBy: "latest_time", sortDir: "desc" }}
    onSort={onSort}
    getRowKey={(row, index) => JSON.stringify([row.source_id, row.log_session_id, row.job_id, row.dataflow_run_id, row.file_uri, row.record_line, row.section, index])}
    columns={[
      { key: capture ? "dataflow_run_id" : "job_id", label: capture ? "Affected run" : "Affected job", sortable: true, minWidth: 180, maxWidth: 300, render: (row) => <FindingObjectCell object={row as unknown as DiagnosticsFindingObject} /> },
      { key: "source_id", label: "Source", sortable: true, autoFit: true, render: (row) => row.source_id == null ? "—" : diagnosticsSourceLabel(`source:${String(row.source_id)}`) },
      { key: "occurrences", label: "Detail", sortable: false, minWidth: 260, fillPriority: "last", render: (row) => { const text = findingObjectIssueText(row as unknown as DiagnosticsFindingObject); return <Help className="diagnostics-issue-cell" title={text}>{text}</Help>; } },
      { key: "latest_time", label: "Latest", sortable: true, autoFit: true, minWidth: 148, maxWidth: 184, render: (row) => formatTime(row.latest_time, timezoneName) },
    ]}
    maxRows={Math.max(1, rows.length)}
    offset={offset}
    timezoneName={timezoneName}
    onRowClick={(row) => onInspect(row as unknown as DiagnosticsFindingObject)}
    className="diagnostics-queue-table monitoring-table-one-line"
  />;
}

function FindingObjectCell({ object }: { object: DiagnosticsFindingObject }) {
  if (object.rule === "field_completeness") {
    const label = findingSectionLabel(object.rule, object.section) ?? "Field group";
    return <span className="monitoring-stack-cell diagnostics-object-cell"><Help as="strong" className="monitoring-ellipsis" title={label}>{label}</Help></span>;
  }
  const primary = object.rule === "diagnostic_capture" ? String(object.dataflow_name ?? object.dataflow_id ?? object.dataflow_run_id ?? "—") : String(object.job_id ?? "—");
  const secondary = object.rule === "diagnostic_capture"
    ? (object.dataflow_run_id ? `run ${object.dataflow_run_id}` : "")
    : object.log_session_id === null || object.log_session_id === undefined ? "no session" : `session ${object.log_session_id === "" ? "(empty)" : object.log_session_id}`;
  return <span className="monitoring-stack-cell diagnostics-object-cell"><Help as="strong" className="monitoring-ellipsis" title={primary}>{primary}</Help>{secondary ? <Help as="small" className="monitoring-ellipsis" title={secondary}>{secondary}</Help> : null}</span>;
}

function DiagnosticCapturePanel({ capture, evidenceOffset, evidenceLimit, timezoneName, onInspect }: {
  capture: DiagnosticsRow | null;
  evidenceOffset: number;
  evidenceLimit: number;
  timezoneName: string | null;
  onInspect?: (row: DiagnosticsRow) => void;
}) {
  if (!capture) return <div className="table-empty">No captured detail summary is available.</div>;
  const evidence = arrayRows(capture.evidence);
  const inspectCapture = (row: DiagnosticsRow) => onInspect?.({
    ...row,
    category: "diagnostic capture",
    severity: row.severity ?? "warning",
    issue: row.issue ?? row.reason ?? row.message ?? "Diagnostic detail captured",
    evidence: row,
  });
  return (
    <div className="diagnostics-capture-panel">
      {!evidence.length ? <div className="table-empty">No captured detail records available in the current filters.</div> : <div className="diagnostics-capture-grid">
        <div className="diagnostics-capture-evidence">
          <DataTable
            rows={evidence}
            offset={evidenceOffset}
            compactNumbers
            compactNumberStyle="infix"
            columns={[
              { key: "source_id", label: "Source", sortable: true, autoFit: true, render: (row) => <Help className="monitoring-ellipsis" title={String(row._source_id ?? row.source_id ?? "")}>{diagnosticsSourceLabel(row._source_id ?? row.source_id)}</Help> },
              { key: "dataflow_run_id", label: "Affected run", sortable: true, minWidth: 156, maxWidth: 260, fillPriority: "normal", render: (row) => <AffectedRunCell row={row} /> },
              { key: "field", label: "Field", sortable: true, autoFit: true, minWidth: 100, maxWidth: 180, render: (row) => <Help className="monitoring-ellipsis" title={captureFieldLabel(row.field)}>{captureFieldLabel(row.field)}</Help> },
              { key: "reason", label: "Reason", sortable: true, minWidth: 180, fillPriority: "last", render: (row) => <Help className="diagnostics-issue-cell" title={String(row.reason ?? row.message ?? "")}>{String(row.reason ?? row.message ?? "—")}</Help> },
              { key: "latest_time", label: "Latest", sortable: true, autoFit: true, minWidth: 148, render: (row) => formatTime(firstMetric(row, "latest_time", "latest_observed_at"), timezoneName) }
            ]}
            maxRows={evidenceLimit}
            onRowClick={inspectCapture}
            className="diagnostics-compact-table diagnostics-capture-table monitoring-table-one-line"
          />
        </div>
      </div>}
    </div>
  );
}

function AffectedRunCell({ row }: { row: DiagnosticsRow }) {
  const name = String(firstMetric(row, "dataflow_name", "run_name", "dataflow_id") ?? "—");
  const runId = String(firstMetric(row, "dataflow_run_id", "run_id") ?? "");
  return <span className="monitoring-stack-cell diagnostics-object-cell"><Help as="strong" className="monitoring-ellipsis" title={name}>{name}</Help>{runId ? <Help as="small" className="monitoring-ellipsis" title={runId}>run {runId}</Help> : null}</span>;
}

function captureFieldLabel(value: unknown) {
  return value === "" ? "Diagnostic marker list" : typeof value === "string" && value.trim() ? value.trim() : "Field unavailable";
}

function formatTime(value: unknown, timezoneName: string | null) {
  return value === null || value === undefined || value === "" ? "—" : formatTimestampForDisplay(value, timezoneName);
}

function asRecord(value: unknown): DiagnosticsRow | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as DiagnosticsRow : null;
}

function arrayRows(value: unknown): DiagnosticsRow[] {
  return Array.isArray(value) ? value.filter((row): row is DiagnosticsRow => Boolean(row && typeof row === "object" && !Array.isArray(row))) : [];
}
