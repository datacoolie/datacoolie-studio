import { ArrowRight } from "lucide-react";
import { lifecycleStatusPresentation } from "../../../shared/statusPresentation";
import { StatusCell } from "../../../shared/components/DataTable";
import { Help } from "../../../shared/components/Help";
import { formatTooltipNumber, metricDifference, type MetricValue } from "../../../shared/lib/numberFormat";
import { monitoringDrawerRawDimensionLabel } from "../monitoringDimensions";
import { compactDrawerIdentifier, dataflowEndpointSummary, DataflowRouteEndpoint } from "./detailPrimitives";
import { DataflowCopyValue } from "./DataflowCopyValue";
import { phaseContributionBottleneck, recordedPhaseTiming } from "../monitoringPhaseContribution";
import { StoryBytes, StoryCard, StoryCount, StoryDuration } from "./DataflowStoryPrimitives";
import { StoryCoverage, StoryFields, type StoryField } from "./DataflowStoryValues";
import { storyLabel, storyMessages, storyMetric, storyMetricFields, storyNumber, storyOutcome, storyPhases, storyRecord, storyScalar, storyStatus, type DataflowStory } from "./dataflowStoryModel";
import { WatermarkBadge } from "../components/monitoringPresentation";

export function DataflowRunRoute({ model }: { model: DataflowStory }) {
  return <section className="monitoring-detail-section monitoring-dataflow-route-card dataflow-run-route">
    <DataflowRouteEndpoint direction="source" endpoint={dataflowEndpointSummary(model.root, "source")} />
    <ArrowRight size={16} aria-hidden="true" />
    <DataflowRouteEndpoint direction="destination" endpoint={dataflowEndpointSummary(model.root, "destination")} />
  </section>;
}

export function DataflowRunSummary({ model, timezoneName, onRetry }: {
  model: DataflowStory; timezoneName?: string | null; onRetry?: () => void;
}) {
  const row = model.root;
  const messages = storyMessages(row,null,-1,model.diagnostics,true);
  return <section className="monitoring-detail-section dataflow-story-summary">
    <h3>Summary &amp; insights</h3>
    {model.availability !== "ready" ? <div className="dataflow-story-availability" role="status">
      {model.availability === "loading" ? "Loading execution details…" : model.availability === "error" ? "Execution details could not be loaded." : "Execution details not found for this run."}
      {model.availability === "error" && onRetry ? <button type="button" className="small-button" onClick={onRetry}>Retry</button> : null}
    </div> : null}
    <div className="dataflow-summary-sections dataflow-summary-three-columns">
      <section className="dataflow-section-block dataflow-run-summary">
      <StoryCard title="Run summary" rows={[
        ["Status", <StatusCell row={{status:storyStatus(row.status)}} />],
        ["Run ID", <SummaryRunId value={row.dataflow_run_id} />],
        ["Start", model.root.start_time ?? "unk", "start_time"],
        ["End", model.root.end_time ?? "unk", "end_time"],
        [["running", "pending"].includes(storyStatus(row.status).toLowerCase()) ? "Reported duration" : "Total duration", <StoryDuration value={row.duration_seconds} />],
        ["Attempts", <StoryCount value={model.attempts?.length} />],
        ["Retries", <StoryCount value={model.root.retry_attempts} />],
        ["Stage", monitoringDrawerRawDimensionLabel(row.stage)], ["Operation", monitoringDrawerRawDimensionLabel(row.operation_type)], ["Load type", monitoringDrawerRawDimensionLabel(row.destination_load_type)],
      ]} timezoneName={timezoneName} />
      </section>
      <SummaryWorkload model={model} />
      <DataflowSummaryInsight model={model} />
    </div>
    {messages.length ? <SummaryMessage model={model} message={messages[0]} /> : null}
    {model.diagnostics?.length ? <p className="dataflow-run-coverage">Some log details were omitted, truncated or invalid. See Technical details.</p> : null}
  </section>;
}

/** All statuses contribute recorded final phase time and observed attempt overhead. */
export function summaryPhaseTiming(model: DataflowStory) {
  return recordedPhaseTiming(model.native, model.availability === "ready");
}

export function DataflowSummaryInsight({ model }: {model:DataflowStory}) {
  const timing=summaryPhaseTiming(model);
  const row=model.root;
  const outcome=storyOutcome(model);
  const resolved=phaseContributionBottleneck(row,timing);
  const reported=resolved.reported;
  const derived=reported ? [] : resolved.phases;
  const bottleneck=resolved.label;
  const watermark=storyRecord(model.final?.watermark);
  const selection=storyRecord(storyRecord(model.final?.source)?.selection);
  const movement=typeof row.movement_state==="string" && row.movement_state.trim() ? row.movement_state : "unknown";
  const throughput=storyNumber(row.performance_rows_per_second);
  return <section className="dataflow-section-block dataflow-summary-insights"><h4>Insight</h4>
    <StoryFields fields={[
      {label:"Bottleneck",value:bottleneck,path:"/performance_bottleneck_phase",content:<Help title={reported ? "Reported performance bottleneck." : derived.length ? `Largest contribution to recorded final phase time${timing.measured<4 ? "; partial timing" : ""}.` : "No positive phase timing or reported bottleneck available."}>{reported ? <InsightPhaseName phase={reported} /> : derived.length ? derived.map((phase,index)=><span key={phase}>{index ? " · " : ""}<InsightPhaseName phase={phase} /></span>) : "unk"}</Help>},
    ]} />
    <div className="dataflow-insight-phase"><Help as="h5" title="Contribution to recorded final-attempt phase time">Contribution</Help>
      {timing.proportional ? <div className="dataflow-insight-phase-bar" role="img" aria-label="Final attempt recorded phase time contribution">{timing.phases.filter(phase=>phase.share !== null && phase.share>0).map(phase=><span key={phase.phase} className={`phase-${phase.phase}`} style={{width:`${phase.share}%`}} />)}</div> : null}
      <div className="dataflow-insight-phase-legend" aria-label="Final attempt phase contribution legend">{timing.phases.map(phase=><Help key={phase.phase} data-phase={phase.phase} title={`${storyLabel(phase.phase)}: ${phase.status ?? "recorded overhead"}; ${phase.duration===null ? "duration unavailable" : `${formatTooltipNumber(phase.duration)} seconds`}`}><span className="dataflow-insight-phase-label"><i className={`phase-${phase.phase}`} aria-hidden="true" /><span aria-label={storyLabel(phase.phase)}>{phase.phase.slice(0,1).toUpperCase()}</span></span><span className="dataflow-insight-phase-values"><span className="dataflow-insight-phase-share">{phase.share===null ? "—" : `${phase.share.toFixed(1)}%`}</span><span className="dataflow-insight-phase-duration">{phaseDurationLabel(phase.duration)}</span></span></Help>)}</div>
      {timing.unavailable || timing.measured===4 && timing.total===0 ? <p className="dataflow-insight-coverage">{timing.unavailable ? "Phase timing unavailable." : "Recorded phase time is zero."}</p> : null}
    </div>
    {storyStatus(row.status).toLowerCase()==="failed" ? <StoryFields className="dataflow-insight-failure" fields={[
      {label:"Failure phase",value:outcome.failurePhase ?? "unk",path:"/failure_phase",content:<span className="dataflow-insight-failure-value" style={{background:lifecycleStatusPresentation("failed")?.pillBackground}}><InsightPhaseName phase={outcome.failurePhase ?? "unk"} /></span>},
    ]} /> : null}
    <div className="dataflow-insight-watermark"><h5>Read &amp; watermark</h5>
      <StoryFields diagnostics={model.diagnostics} fields={[
        {label:"Read mode",value:selection?.mode,path:`/attempts/${model.finalIndex}/source/selection/mode`,text:true},
        {label:"Movement",value:movement,path:"/movement_state",content:<WatermarkBadge value={movement} />},
      ]} />
      <SummaryPair diagnostics={model.diagnostics} fields={[
        {label:"Load",value:watermark?.load_status,path:`/attempts/${model.finalIndex}/watermark/load_status`,content:storyStatus(watermark?.load_status)},
        {label:"Save",value:watermark?.save_status,path:`/attempts/${model.finalIndex}/watermark/save_status`,content:storyStatus(watermark?.save_status)},
      ]} />
    </div>
    {((typeof row.performance_candidate_reason==="string" && row.performance_candidate_reason.trim()) || (throughput !== null && throughput >= 0)) ? <div className="dataflow-insight-performance"><h5>Performance</h5><StoryFields fields={[
      ...(typeof row.performance_candidate_reason==="string" && row.performance_candidate_reason.trim() ? [{label:"Signal",value:row.performance_candidate_reason,path:"/performance_candidate_reason",text:true}] : []),
      ...(throughput !== null && throughput >= 0 ? [{label:"Rows / second",value:row.performance_rows_per_second,path:"/performance_rows_per_second",content:<StoryCount value={row.performance_rows_per_second} />}] : []),
    ]} /></div> : null}
  </section>;
}

function InsightPhaseName({phase}:{phase:string}) {
  const normalized=phase.toLowerCase();
  return [...storyPhases,"overhead"].includes(normalized) ? <span className={`dataflow-insight-phase-name name-${normalized}`}>{storyLabel(normalized)}</span> : <span>{phase}</span>;
}

function SummaryRunId({value}:{value:unknown}) {
  const id=typeof value==="string" && value.trim() ? value : null;
  const display=compactDrawerIdentifier(id) ?? "unk";
  return <span className="dataflow-summary-run-id">{id ? <DataflowCopyValue label="Run ID" value={id} title={id}>{display}</DataflowCopyValue> : <span>unk</span>}</span>;
}

function SummaryWorkload({model}:{model:DataflowStory}) {
  const groups=[{title:"Rows",pairs:[["rows_read","rows_written"],["rows_inserted","rows_updated","rows_deleted"]]},{title:"Files",pairs:[["files_added","files_removed"]]},{title:"Bytes",pairs:[["bytes_added","bytes_removed"],["bytes_saved","net_bytes"]]}] as const;
  const labels:Record<string,string>={read:"Read",written:"Written",inserted:"Insert",updated:"Update",deleted:"Delete",added:"Add",removed:"Remove",saved:"Saved",net_bytes:"Net"};
  return <section className="dataflow-section-block dataflow-run-workload"><h4>Run workload <StoryCoverage diagnostics={model.diagnostics} path="/metrics" /></h4>{groups.map(group=><div className="dataflow-workload-group" key={group.title}><h5>{group.title}</h5>{group.pairs.map((pair,index)=><SummaryPair key={index} diagnostics={model.diagnostics} fields={pair.map(field=>{
    const action=field==="net_bytes" ? field : field.slice(field.indexOf("_")+1);
    const value=field==="net_bytes" ? metricDifference(storyScalar(storyMetric(model,"bytes_added")) as MetricValue,storyScalar(storyMetric(model,"bytes_removed")) as MetricValue) : storyMetric(model,field as typeof storyMetricFields[number]);
    return {label:labels[action],value,path:field==="net_bytes" ? "/summary/net_bytes" : `/metrics/${field}`,content:<span className={`dataflow-metric-action action-${action}`}>{field.startsWith("bytes") || field==="net_bytes" ? <StoryBytes value={value} /> : <StoryCount value={value} />}</span>};
  })} />)}</div>)}</section>;
}

function SummaryPair({fields,diagnostics}:{fields:StoryField[];diagnostics?:unknown[] | null}) {
  return <dl className="dataflow-story-fields dataflow-summary-pair"><div><dt>{fields.map((field,index)=><span key={field.path}>{index ? <span className="dataflow-pair-divider" aria-hidden="true"> | </span> : null}{field.label}<StoryCoverage diagnostics={diagnostics} path={field.path} /></span>)}</dt><dd>{fields.map((field,index)=><span key={field.path}>{index ? <span className="dataflow-pair-divider" aria-hidden="true"> | </span> : null}<span data-field-path={field.path} aria-label={field.label}>{field.content}</span></span>)}</dd></div></dl>;
}

function SummaryMessage({model,message}:{model:DataflowStory;message:ReturnType<typeof storyMessages>[number]}) {
  const status=lifecycleStatusPresentation(model.root.status);
  return <section className="dataflow-section-block dataflow-summary-message" style={status ? {background:status.drawerSurface} : undefined}><h4>Message</h4><div className="dataflow-summary-message-value monitoring-message-scroll">{message.text || "unk"}</div>{message.truncatedLabels.length ? <small>Message truncated in log.</small> : null}{message.coverage?.filter(issue=>issue.reason!=="truncated").map((issue,index)=><small key={index}>Message {issue.reason} in log.</small>)}</section>;
}

function phaseDurationLabel(seconds: number | null) {
  if(seconds===null) return "—";
  const divisor=seconds>=86400 ? 86400 : seconds>=3600 ? 3600 : seconds>=60 ? 60 : 1;
  const unit=divisor===86400 ? "d" : divisor===3600 ? "h" : divisor===60 ? "min" : "s";
  return `${(seconds/divisor).toLocaleString("en-US",{minimumFractionDigits:1,maximumFractionDigits:1})} ${unit}`;
}
