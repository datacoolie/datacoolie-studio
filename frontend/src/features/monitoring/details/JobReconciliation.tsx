import { DataTable, type TableColumn } from "../../../shared/components/DataTable";
import { Help } from "../../../shared/components/Help";
import { StoryBytes, StoryCount, StoryDisclosure } from "./DataflowStoryPrimitives";
import { StoryFields } from "./DataflowStoryValues";
import { formatTimestampForDisplay } from "../../../shared/time";
import { ReconciliationBadge } from "../pages/JobsPageSupport";
import { jobComparisonConclusion, jobComparisonRows, jobReconciliationNote, jobReconciliationScopeLabel, jobReconciliationStatus, type JobStory } from "./jobStoryModel";

type ComparisonRow = ReturnType<typeof jobComparisonRows>[number];

export function JobReconciliation({ model, timezoneName }: { model: JobStory; timezoneName?: string | null }) {
  return <StoryDisclosure title="Reconciliation" className="job-reconciliation">
    {() => <JobReconciliationContent model={model} timezoneName={timezoneName} />}
  </StoryDisclosure>;
}

export function JobReconciliationContent({ model, timezoneName }: { model: JobStory; timezoneName?: string | null }) {
  const evidence = model.evidence;
  const rows = jobComparisonRows(model);
  const count = rows.find(row => row.metric === "Dataflows");
  const status = jobReconciliationStatus(evidence);
  const timestamp = (value: unknown) => value ? formatTimestampForDisplay(String(value), timezoneName) : "unk";
  return <>
    <div className="job-two-columns job-reconciliation-summary">
      <section className="job-section-block"><h4>Result</h4><StoryFields fields={[
        { label: "Reconcile", path: "/reconciliation_status", value: status, content: <ReconciliationBadge value={status} /> },
        { label: "Check", path: "/reconciliation_check", value: "Dataflow count", text: true },
        { label: "Conclusion", path: "/comparison", value: null, content: jobComparisonConclusion(model) },
        { label: "Job log", path: "/comparison/total_dataflows", value: count?.reported, content: <StoryCount value={count?.reported} /> },
        { label: "Child logs", path: "/child_dataflow_count", value: count?.observed, content: <StoryCount value={count?.observed} /> },
        { label: "Difference", path: "/comparison/difference", value: count?.difference, content: count ? <ComparisonValue row={count} field="difference" /> : "unk" },
      ]} /><p className="job-story-note">{jobReconciliationNote({ ...evidence, total_dataflows: model.comparisonReported.total_dataflows })}</p></section>
      <section className="job-section-block"><h4>Comparison basis</h4><StoryFields fields={[
        { label: "Scope", path: "/child_scope_coverage", value: jobReconciliationScopeLabel(evidence), text: true },
        { label: "Job snapshot", path: "/observed_at", value: null, content: timestamp(evidence.observed_at) },
        { label: "Latest child", path: "/latest_child_time", value: null, content: timestamp(evidence.latest_child_time) },
        { label: "Records dropped", path: "/log_records_dropped", value: evidence.log_records_dropped, content: <StoryCount value={evidence.log_records_dropped} /> },
        { label: "Bytes dropped", path: "/log_bytes_dropped", value: evidence.log_bytes_dropped, content: <StoryBytes value={evidence.log_bytes_dropped} /> },
      ]} /></section>
    </div>
    {rows.length ? <section className="job-section-block job-reconciliation-group" aria-label="Metric comparisons">
      <h4>Metric comparisons</h4>
      <p className="job-story-note">Only Dataflow count determines Reconcile. Rows exclude maintenance; counts and bytes include all operations.</p>
      <DataTable rows={rows} columns={[
        {
          key: "group", label: "Group", sortable: true, autoFit: true, minWidth: 100, fillPriority: "normal",
          render: (row: ComparisonRow) => <span className={`job-reconciliation-group-label is-${row.group.toLowerCase().replace(/\s+/gu, "-")}`}>{row.group}</span>,
        },
        {
          key: "metric", label: "Metric", sortable: true, autoFit: true, minWidth: 92, fillPriority: "normal",
          render: (row: ComparisonRow) => <strong className="job-reconciliation-metric-value">{row.metric}</strong>,
        },
        ...(["reported", "observed", "difference"] as const).map(field => ({
          key: field, label: field === "reported" ? "Job log" : field === "observed" ? "Child logs" : "Difference",
          sortable: true, autoFit: true, minWidth: 78, fillPriority: "normal" as const,
          render: (row: ComparisonRow) => <ComparisonValue row={row} field={field} />,
        })),
        {
          key: "coverage", label: "Measured / eligible", autoFit: true, minWidth: 116, fillPriority: "normal",
          render: (row: ComparisonRow) => row.eligible === undefined
            ? <span className="job-reconciliation-coverage-na">—</span>
            : <span className="job-reconciliation-coverage"><StoryCount value={row.measured} /> / <StoryCount value={row.eligible} /></span>,
        },
      ] as TableColumn<ComparisonRow>[]} maxRows={rows.length} className="job-reconciliation-table monitoring-table-one-line" />
    </section> : <p className="job-story-note">Comparison measurements unavailable.</p>}
  </>;
}

function ComparisonValue({ row, field }: { row: ComparisonRow; field: "reported" | "observed" | "difference" }) {
  const different = field === "difference" && row.difference !== null && row.difference !== "0";
  return <Help className={`job-reconciliation-value${different ? " is-different" : ""}`} title={field === "difference" ? row.differenceNote ?? undefined : undefined}>
    {row.metric.startsWith("Bytes") ? <StoryBytes value={row[field]} /> : <StoryCount value={row[field]} />}
  </Help>;
}
