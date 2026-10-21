import { StoryCount, StoryDisclosure } from "./DataflowStoryPrimitives";
import { StoryCoverage, StoryFields, type StoryField } from "./DataflowStoryValues";
import { storyIssues, storyRecord, storyStatus, type DataflowStory, type StoryRecord } from "./dataflowStoryModel";
import { WatermarkBadge } from "../components/monitoringPresentation";
import { Help } from "../../../shared/components/Help";

export function DataflowWatermarkSummary({ model }: { model: DataflowStory }) {
  const watermark = storyRecord(model.final?.watermark);
  const movement = typeof model.root.movement_state === "string" && model.root.movement_state.trim() ? model.root.movement_state : "unknown";
  const path = `/attempts/${model.finalIndex}/watermark`;
  return <div className="dataflow-run-watermark">
    <span>Watermark <WatermarkBadge value={movement} /></span>
    <Help title="Final attempt">Final Load: {storyStatus(watermark?.load_status)} <StoryCoverage diagnostics={model.diagnostics} path={`${path}/load_status`} /></Help>
    <Help title="Final attempt">Final Save: {storyStatus(watermark?.save_status)} <StoryCoverage diagnostics={model.diagnostics} path={`${path}/save_status`} /></Help>
    <StoryCoverage diagnostics={model.diagnostics} path={path} />
  </div>;
}

export function DataflowReadWatermark({ model }: { model: DataflowStory }) {
  const request = model.native?.read_request;
  const selection = storyRecord(model.final?.source)?.selection;
  const watermark = storyRecord(model.final?.watermark);
  const path = `/attempts/${model.finalIndex}`;
  if (!model.final && request == null && selection == null && !watermark && !storyIssues(model.diagnostics, "/read_request").length && !storyIssues(model.diagnostics, `${path}/source/selection`).length && !storyIssues(model.diagnostics, `${path}/watermark`).length) return null;
  return <section className="monitoring-detail-section dataflow-story-watermark" id="dataflow-read-watermark">
    <StoryDisclosure title={<span>Read &amp; watermark <small>Final attempt <StoryCount value={model.final?.attempt_number} /></small> <StoryCoverage diagnostics={model.diagnostics} path="/read_request" /> <StoryCoverage diagnostics={model.diagnostics} path={`${path}/source/selection`} /> <StoryCoverage diagnostics={model.diagnostics} path={`${path}/watermark`} /></span>}>{()=> <DataflowReadWatermarkBody model={model} />}</StoryDisclosure>
  </section>;
}

export function DataflowReadWatermarkBody({ model }: { model: DataflowStory }) {
  return <StoryReadValues request={model.native?.read_request} selection={storyRecord(model.final?.source)?.selection} watermark={model.final?.watermark} movement={model.root.movement_state} attemptIndex={model.finalIndex} diagnostics={model.diagnostics} />;
}

/** Explicit attempt inputs prevent historical evidence from reading final data. */
export function StoryReadValues({ request, selection, watermark, movement, attemptIndex, diagnostics, includeRequest = true }: { request?: unknown; selection?: unknown; watermark?: unknown; movement?: unknown; attemptIndex: number; diagnostics?: unknown[] | null; includeRequest?: boolean }) {
  const selectionPath = `/attempts/${attemptIndex}/source/selection`;
  const watermarkPath = `/attempts/${attemptIndex}/watermark`;
  const requestRecord = storyRecord(request);
  const saved = storyRecord(watermark);
  const showRequest = includeRequest && (request != null || storyIssues(diagnostics, "/read_request").length > 0);
  const showSelection = selection != null || storyIssues(diagnostics, selectionPath).length > 0;
  const showWatermark = watermark != null || storyIssues(diagnostics, watermarkPath).length > 0;
  return <div className="dataflow-story-read-values">
    <section className="dataflow-section-block dataflow-read-region"><h4>Read {includeRequest ? <StoryCoverage diagnostics={diagnostics} path="/read_request" /> : null} <StoryCoverage diagnostics={diagnostics} path={selectionPath} /></h4>
      {!showRequest && !showSelection ? <p className="monitor-muted">Read detail unavailable.</p> : null}
      <StoryFields expandStructured diagnostics={diagnostics} fields={[
        {label:"Mode",value:storyRecord(selection)?.mode,path:`${selectionPath}/mode`,content:drawerValue(storyRecord(selection)?.mode)},
        ...(includeRequest ? requestRecord ? fieldsFrom(requestRecord,"/read_request",[["Lower","watermark_start"],["Lower operator","watermark_start_operator"],["Upper","watermark_end"],["Upper operator","watermark_end_operator"],["Read range","read_range"],["Reference time","reference_time"]],diagnostics) : [{label:"Read request",value:request,path:"/read_request"}] : []),
        {label:"Selection",value:selection,path:selectionPath},
      ]} />
    </section>
    <section className="dataflow-section-block dataflow-watermark-region"><h4>Watermark <StoryCoverage diagnostics={diagnostics} path={watermarkPath} /></h4>
      <dl className="dataflow-watermark-overview dataflow-highlight-fields">
        <div><dt>Movement</dt><dd><WatermarkBadge value={typeof movement === "string" && movement.trim() ? movement : "unknown"} /></dd></div>
        <div><dt>Load | Save</dt><dd><span data-field-path={`${watermarkPath}/load_status`}>{storyStatus(saved?.load_status)} <StoryCoverage diagnostics={diagnostics} path={`${watermarkPath}/load_status`} /></span><span className="monitor-muted"> | </span><span data-field-path={`${watermarkPath}/save_status`}>{storyStatus(saved?.save_status)} <StoryCoverage diagnostics={diagnostics} path={`${watermarkPath}/save_status`} /></span></dd></div>
      </dl>
      {!showWatermark ? <p className="monitor-muted">Watermark detail unavailable.</p> : null}
      <StoryFields expandStructured diagnostics={diagnostics} fields={saved ? fieldsFrom(saved,watermarkPath,[["Loaded watermark","loaded_watermark"],["Merge base","merge_base"],["Candidate","candidate"],["Watermark to save","watermark_to_save"],["Saved watermark","saved_watermark"]],diagnostics).filter(field => ![`${watermarkPath}/load_status`, `${watermarkPath}/save_status`].includes(field.path)) : [{label:"Watermark",value:watermark,path:watermarkPath}]} />
    </section>
  </div>;
}

function drawerValue(value: unknown) {
  return typeof value === "string" && value.trim() ? value : value == null ? "unk" : String(value);
}

function fieldsFrom(record: StoryRecord, path: string, fields: string[][], diagnostics?: unknown[] | null): StoryField[] {
  const labels=new Map(fields.map(([label,key])=>[key,label]));
  const issueKeys=storyIssues(diagnostics,path).map(issue=>issue.field.slice(path.length+1).split("/")[0].replace(/~1/g,"/").replace(/~0/g,"~")).filter(Boolean);
  return [...new Set([...fields.map(([,key])=>key),...Object.keys(record),...issueKeys])].map(key=>({label:labels.get(key) ?? key.replace(/_/g," ").replace(/^./,letter=>letter.toUpperCase()),value:record[key],path:`${path}/${key.replace(/~/g,"~0").replace(/\//g,"~1")}`,text:true}));
}
