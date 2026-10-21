import { useMemo, useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import { buildDataflowStory, noStoryAttemptMessage, storyRecord, storyScalar, type DataflowStory, type DetailAvailability, type StoryRecord } from "./dataflowStoryModel";
import { StoryDisclosure, StoryJson } from "./DataflowStoryPrimitives";
import { DataflowStoryConfiguration } from "./DataflowStoryConfiguration";
import { DataflowReadWatermark } from "./DataflowStoryWatermark";
import { ExecutionAttempts } from "./DataflowAttemptExecution";
import { DataflowRunRoute, DataflowRunSummary } from "./DataflowRunSummary";
import { StoryExactValue, StoryFields, type StoryField } from "./DataflowStoryValues";
import { copyToClipboard, safeJsonStringify } from "./detailPrimitives";
import { DataflowCopyValue } from "./DataflowCopyValue";
import { Help } from "../../../shared/components/Help";

export function DataflowDetailSections({ row, timezoneName, availability = "ready", onRetry }: {
  row: StoryRecord; timezoneName?: string | null; availability?: DetailAvailability; onRetry?: () => void;
}) {
  const model = useMemo(() => buildDataflowStory(row, availability), [row, availability]);
  return <>
    <DataflowRunRoute model={model} />
    <DataflowRunSummary model={model} timezoneName={timezoneName} onRetry={onRetry} />
    <section className="monitoring-detail-section dataflow-story-execution">
      <h3>Execution <small>Latest attempt first</small></h3>
      {model.attempts?.length ? <ExecutionAttempts model={model} timezoneName={timezoneName} /> : <p className="monitor-muted">{noStoryAttemptMessage(model)}</p>}
    </section>
    <DataflowReadWatermark model={model} />
    <DataflowStoryConfiguration row={model.root} timezoneName={timezoneName} diagnostics={model.diagnostics} />
    <StoryDisclosure className="dataflow-story-technical" title="Technical details">{() => <DataflowTechnicalBody model={model} row={row} />}</StoryDisclosure>
  </>;
}

export function DataflowTechnicalBody({ model, row }: {model:DataflowStory;row:StoryRecord}) {
  const root=model.root;
  return <>
    <section className="dataflow-section-block dataflow-technical-identity"><h4>Identity and log context</h4>
      <TechnicalFields fields={[
        {label:"Run ID",value:root.dataflow_run_id,path:"/dataflow_run_id"},
        {label:"Dataflow ID",value:root.dataflow_id,path:"/dataflow_id"},
        {label:"Job ID",value:root.job_id,path:"/job_id"},
        {label:"Session ID",value:root.log_session_id,path:"/log_session_id"},
        {label:"Log source ID",value:row._source_id,path:"/_source_id"},
        {label:"Workspace ID",value:root.workspace_id,path:"/workspace_id"},
        {label:"Job number",value:root.job_num,path:"/job_num"},
        {label:"Job index",value:root.job_index,path:"/job_index"},
        {label:"Record type",value:root._type,path:"/_type"},
        {label:"DataCoolie version",value:root.datacoolie_version,path:"/datacoolie_version"},
        {label:"Log schema version",value:root.log_schema_version,path:"/log_schema_version"},
        {label:"File",value:row._file_uri,path:"/_file_uri",content:typeof row._file_uri === "string" ? row._file_uri : undefined},
        {label:"Record line",value:row._record_line,path:"/_record_line"},
      ]} />
    </section>
    {model.diagnostics?.length ? <DiagnosticDetails model={model} /> : null}
    <StoryDisclosure title={<Help title="Execution log record stored by Studio.">Execution JSON</Help>}>{()=> <StoryJson value={model.native ?? model.root} copyPayload={model.payload} />}</StoryDisclosure>
  </>;
}

function TechnicalFields({fields}: {fields:StoryField[]}) {
  return <StoryFields fields={fields.map(field=>({...field,content:<DataflowCopyValue label={field.label} value={field.value}>{field.content}</DataflowCopyValue>}))} />;
}

function TechnicalCopyValue({label,value,children}: {label:string;value:unknown;children?:ReactNode}) {
  const [copied,setCopied]=useState(false);
  const scalar=storyScalar(value);
  const exact=value===undefined ? "unk" : typeof scalar==="string" ? scalar : safeJsonStringify(value);
  return <span className="dataflow-technical-copy-value"><span>{children ?? <StoryExactValue value={value} text />}</span><Help as="button" type="button" className="icon-action small" aria-label={`Copy ${label}`} title={`Copy ${label}`} onClick={()=>{void copyToClipboard(exact,setCopied);}}>{copied ? <Check size={12}/> : <Copy size={12}/>}</Help></span>;
}

export function DiagnosticDetails({ model }: { model: DataflowStory }) {
  return <section className="dataflow-story-diagnostics"><h4>Log detail coverage</h4>
    <p className="monitor-muted">These issues describe captured log detail, not the execution result. Detail coverage does not change the run status.</p>
    {model.diagnostics?.some((value) => { const record = storyRecord(value); return record?.field === "/diagnostic_issues" && record.reason === "omitted"; }) ? <p className="monitor-muted">Additional issue count is unknown because the issue list was truncated.</p> : null}
    <dl>{model.diagnostics!.map((value, index) => {
      const record = storyRecord(value);
      return <div key={index}><dt>{String(record?.field ?? "Unknown field")}</dt><dd><TechnicalCopyValue label={String(record?.field ?? "Unknown field")} value={record?.reason} /></dd></div>;
    })}</dl>
  </section>;
}
