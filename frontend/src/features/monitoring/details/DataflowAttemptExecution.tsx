import { StatusCell } from "../../../shared/components/DataTable";
import { Help } from "../../../shared/components/Help";
import { lifecycleStatusPresentation } from "../../../shared/statusPresentation";
import { phaseRuntimeStatusClass, formatRuntimeTimestampForDisplay } from "./detailPrimitives";
import { storyAttemptsDescending, storyIssues, storyLabel, storyMessages, storyMetricFields, storyPhases, storyRecord, storyStatus, type DataflowStory, type StoryMessage, type StoryRecord } from "./dataflowStoryModel";
import { StoryBytes, StoryCount, StoryDisclosure, StoryDuration } from "./DataflowStoryPrimitives";
import { StoryCoverage, StoryExactValue, StoryExpandedValue, StoryFields, type StoryField } from "./DataflowStoryValues";

export function ExecutionAttempts({ model, timezoneName }: { model: DataflowStory; timezoneName?: string | null }) {
  return <div className="dataflow-execution-attempts">
    {storyAttemptsDescending(model).map(({record, nativeIndex: index, isFinal}) => {
      if (!record) return <p key={index} data-attempt-index={index} className="monitor-muted">{isFinal ? "Final attempt" : `Attempt at log position ${index + 1}`}: detail unavailable. <StoryCoverage diagnostics={model.diagnostics} path={`/attempts/${index}`} /></p>;
      const phase = storyPhases.find(phase => storyStatus(storyRecord(record[phase])?.status).toLowerCase() === "failed");
      const status=lifecycleStatusPresentation(record.status);
      return <StoryDisclosure key={index} defaultOpen={isFinal} className="dataflow-execution-attempt" style={status ? {background:status.drawerSurface} : undefined} title={<span className="dataflow-story-attempt-summary" data-attempt-index={index}>
        <strong>Attempt <StoryCount value={record.attempt_number} /></strong><StatusCell row={{ status: storyStatus(record.status) }} />
        {isFinal ? <span className="dataflow-story-final-marker">Final attempt</span> : null}
        <StoryCoverage diagnostics={model.diagnostics} path={`/attempts/${index}`} />
        <StoryDuration value={record.duration_seconds} />{phase ? <span>{storyLabel(phase)}</span> : null}
      </span>}>{() => <AttemptBody record={record} index={index} diagnostics={model.diagnostics} timezoneName={timezoneName} />}</StoryDisclosure>;
    })}
  </div>;
}

export function AttemptBody({ record, index, diagnostics, timezoneName }: { record: StoryRecord; index: number; diagnostics?: unknown[] | null; timezoneName?: string | null }) {
  const messages = storyMessages(record,record,index,diagnostics ?? null);
  const path = `/attempts/${index}`;
  const watermarkPath = `${path}/watermark`;
  return <div className="dataflow-attempt-sections" data-attempt-body={index}>
    <section className="dataflow-section-block dataflow-attempt-overview"><h4>Dataflow <StoryCoverage diagnostics={diagnostics} path={path} /></h4>
      <StoryFields expandStructured diagnostics={diagnostics} fields={recordFields(record,path,["attempt_number","status","start_time","end_time","duration_seconds"],[...storyPhases,"watermark","message"],diagnostics,timezoneName)} />
      <AttemptMessages messages={messages} scope="Attempt" record={record} path={path} diagnostics={diagnostics} />
    </section>
    <div className="dataflow-attempt-phases">
      <PhaseExecution record={record} attemptIndex={index} diagnostics={diagnostics} timezoneName={timezoneName} sections messages={messages} />
      <section className="dataflow-section-block dataflow-attempt-watermark" data-phase-section="watermark"><h4>Watermark <StoryCoverage diagnostics={diagnostics} path={watermarkPath} /></h4>
        {Object.prototype.hasOwnProperty.call(record,"watermark") ? <div data-field-path={watermarkPath}><StoryExpandedValue value={record.watermark} showType={false} /></div>
          : <p className="monitor-muted">{storyIssues(diagnostics,watermarkPath).length ? "Watermark detail unavailable" : "No watermark recorded"}</p>}
      </section>
    </div>
  </div>;
}

export function PhaseExecution({ record, timezoneName, attemptIndex = 0, diagnostics, compact = false, collapsible = false, sections = false, messages = [] }: { record: StoryRecord; timezoneName?: string | null; attemptIndex?: number; diagnostics?: unknown[] | null; compact?: boolean; collapsible?: boolean; sections?: boolean; messages?: StoryMessage[] }) {
  const phases = storyPhases.map(phase => {
    const value = storyRecord(record[phase]);
    const path = `/attempts/${attemptIndex}/${phase}`;
    const fields = value ? recordFields(value,path,["start_time","end_time",...(sections ? ["duration_seconds"] : [])],["status","message",...(sections ? [] : ["duration_seconds"])],diagnostics,timezoneName) : [];
    const summary = <span className="dataflow-run-phase-summary"><strong>{storyLabel(phase)}</strong><StoryCoverage diagnostics={diagnostics} path={path} />{value ? <><span data-field-path={`${path}/status`}><StatusCell row={{ status: storyStatus(value.status) }} /></span>{!sections ? <span data-field-path={`${path}/duration_seconds`}><StoryDuration value={value.duration_seconds} /></span> : null}</> : <span className="monitor-muted">{storyIssues(diagnostics,path).length ? "Phase detail unavailable" : "No phase recorded"}</span>}</span>;
    const body = () => <StoryFields expandStructured={sections} fields={fields} diagnostics={diagnostics} className={compact ? "is-grid" : ""} />;
    return <article key={phase} data-phase-section={phase} className={value ? phaseRuntimeStatusClass(storyStatus(value.status)) : "monitoring-dataflow-runtime-card dataflow-story-phase-absent"}>
      {collapsible && value ? <StoryDisclosure className="dataflow-run-phase-disclosure" title={summary}>{body}</StoryDisclosure>
        : <><header>{summary}</header>{value ? body() : null}</>}
      {sections && value ? <AttemptMessages messages={messages} scope={storyLabel(phase)} record={value} path={path} diagnostics={diagnostics} /> : null}
    </article>;
  });
  return sections ? <>{phases}</> : <div className={`dataflow-story-phase-grid${compact ? " is-history" : ""}`}>{phases}</div>;
}

/** Ordered core fields plus all other captured keys; diagnostics never borrow another attempt. */
function recordFields(record: StoryRecord, path: string, first: string[], exclude: string[], diagnostics?: unknown[] | null, timezoneName?: string | null): StoryField[] {
  const issueKeys = storyIssues(diagnostics,path).map(issue=>issue.field.slice(path.length+1).split("/")[0].replace(/~1/g,"/").replace(/~0/g,"~")).filter(Boolean);
  return [...new Set([...first,...Object.keys(record),...issueKeys])].filter(key=>!exclude.includes(key)).map(key=>{
    const value=record[key];
    const pointer=key.replace(/~/g,"~0").replace(/\//g,"~1");
    return {label:fieldLabel(key),value,path:`${path}/${pointer}`,text:true,content:
      key==="status" ? <StatusCell row={{status:storyStatus(value)}} />
      : ["load_status","save_status"].includes(key) ? storyStatus(value)
      : ["start_time","end_time"].includes(key) && typeof value==="string" && value ? <Help title={value}>{formatRuntimeTimestampForDisplay(value,timezoneName)}</Help>
      : key==="duration_seconds" && value!=null ? <StoryDuration value={value} />
      : (storyMetricFields.includes(key as typeof storyMetricFields[number]) || key==="attempt_number") && value!=null ? key.includes("bytes") ? <StoryBytes value={value} /> : <StoryCount value={value} /> : undefined};
  });
}

function AttemptMessages({ messages, scope, record, path, diagnostics }: { messages: StoryMessage[]; scope: string; record: StoryRecord; path: string; diagnostics?: unknown[] | null }) {
  const message = messages.find(value => value.labels.includes(scope));
  if (!message && !Object.prototype.hasOwnProperty.call(record,"message") && !storyIssues(diagnostics,`${path}/message`).length) return null;
  const status=lifecycleStatusPresentation(record.status);
  const text=message?.text ?? (typeof record.message==="string" ? record.message : null);
  return <StoryFields diagnostics={diagnostics} className="dataflow-attempt-message-field" fields={[{label:"Message",value:record.message,path:`${path}/message`,content:<div className="dataflow-attempt-message-box monitoring-message-scroll" style={status ? {background:status.drawerSurface} : undefined}>
    {text!==null ? text || <StoryExactValue value={record.message} text /> : <StoryExactValue value={record.message} text />}
    {message?.truncatedLabels.includes(scope) || record.message_truncated===true ? <small>{scope}: message truncated in log.</small> : null}
    {message?.coverage?.filter(issue=>issue.label===scope&&issue.reason!=="truncated").map((issue,index)=><small key={index}>{scope}: message {issue.reason} in log.</small>)}
  </div>}]} />;
}

function fieldLabel(key: string) {
  return ({attempt_number:"Attempt",duration_seconds:"Duration",start_time:"Start",end_time:"End",load_status:"Load",save_status:"Save"} as Record<string,string>)[key] ?? key.replace(/_/g," ").replace(/^./,letter=>letter.toUpperCase());
}
