import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { parseRawRecordJson } from "../../../shared/api/monitoringAdapters";
import { StoryExactValue, StoryExpandedValue, storyValuePreview } from "./DataflowStoryValues";
import { DataflowConfigurationBody, DataflowStoryConfiguration } from "./DataflowStoryConfiguration";
import { DataflowReadWatermark, DataflowReadWatermarkBody, StoryReadValues } from "./DataflowStoryWatermark";
import { DataflowDetailSections } from "./DataflowStoryDetails";
import { AttemptBody, ExecutionAttempts, PhaseExecution } from "./DataflowAttemptExecution";
import { buildDataflowStory, storyIssues, storyMetricFields } from "./dataflowStoryModel";

const schema = JSON.parse(readFileSync(new URL("../../../../../tests/fixtures/datacoolie/logging/v6/schema.json", import.meta.url),"utf8")).$defs;

function jsonBoxText(html: string) {
  return html.match(/<pre[^>]*>([\s\S]*?)<\/pre>/)![1].replace(/<[^>]+>/g,"").replace(/&quot;/g,'"').replace(/&#x27;/g,"'").replace(/&lt;/g,"<").replace(/&gt;/g,">").replace(/&amp;/g,"&");
}

describe("Dataflow compact debug values", () => {
  it("keeps original Read fields and shows only Movement then paired final Load/Save above Watermark evidence", () => {
    const model=buildDataflowStory({movement_state:"unchanged",read_request:{watermark_start:"authored-lower"},attempts:[{watermark:{load_status:"old-load",saved_watermark:"old-saved"}},{source:{selection:{mode:"incremental",lower:0,upper:false}},watermark:{load_status:"loaded",save_status:"saved",candidate:{kind:"observed_max",values:0},saved_watermark:false}}]});
    const body=renderToStaticMarkup(<DataflowReadWatermarkBody model={model} />);
    expect(body).toContain('data-field-path="/attempts/1/source/selection/mode"><dt>Mode </dt><dd>incremental</dd>');
    expect(body.indexOf('<dt>Mode ')).toBeLessThan(body.indexOf('<dt>Lower '));
    expect(body).not.toContain('dataflow-read-highlights');expect(body).not.toContain('dataflow-watermark-lifecycle');expect(body).toContain('authored-lower');expect(body).toContain('&quot;incremental&quot;');expect(body).not.toContain('old-load');expect(body).not.toContain('old-saved');
    expect(body.indexOf('<dt>Movement</dt>')).toBeLessThan(body.indexOf('<dt>Load | Save</dt>'));expect(body.indexOf('<dt>Load | Save</dt>')).toBeLessThan(body.indexOf('<dt>Loaded watermark'));
    for(const key of ["load_status","save_status"])expect(body.match(new RegExp(`data-field-path="/attempts/1/watermark/${key}"`,"g"))).toHaveLength(1);
    expect(body).toContain('Unchanged');expect(body).not.toContain('Advanced');expect(body).not.toMatch(/<dt>Load\s*<\/dt>/);expect(body).not.toMatch(/<dt>Save\s*<\/dt>/);
  });

  it("does not infer Movement from save and keeps Load/Save diagnostic pointers scoped", () => {
    const html=renderToStaticMarkup(<StoryReadValues attemptIndex={10} watermark={{save_status:"saved"}} diagnostics={[{field:"/attempts/10/watermark/load_status",reason:"omitted"},{field:"/attempts/1/watermark/save_status",reason:"unrelated"}]} />);
    expect(html.split('<dt>Movement</dt>')[1].split('</dd>')[0]).toContain('Unknown');expect(html).not.toContain('Advanced');expect(html).toContain('saved');expect(html).toContain('/attempts/10/watermark/load_status: omitted');expect(html).not.toContain('unrelated');
  });

  it("shows one exact native Watermark JSON per attempt including extra fields and diagnostics", () => {
    const value=parseRawRecordJson('{"loaded_watermark":9007199254740993,"candidate":{"kind":"opaque","values":"9007199254740993"},"saved_watermark":false,"extra/key":null,"message":"native-message"}');
    const html=renderToStaticMarkup(<AttemptBody index={10} record={{watermark:value}} diagnostics={[{field:"/attempts/10/watermark/extra~1key",reason:"invalid"},{field:"/attempts/1/watermark",reason:"unrelated"}]} />);
    const section=html.split('data-phase-section="watermark"')[1].split('</section>')[0];
    expect(section.match(/<pre/g)).toHaveLength(1);expect(section.match(/aria-label="Copy JSON"/g)).toHaveLength(1);expect(section).not.toContain('<dt>');expect(section).not.toContain('>JSON</span>');expect(section).toContain('/attempts/10/watermark/extra~1key: invalid');expect(section).not.toContain('unrelated');
    expect(jsonBoxText(section)).toBe('{\n  "loaded_watermark": 9007199254740993,\n  "candidate": {\n    "kind": "opaque",\n    "values": "9007199254740993"\n  },\n  "saved_watermark": false,\n  "extra/key": null,\n  "message": "native-message"\n}');
  });

  it.each([null,false,0,[],{}])("preserves captured scalar or empty Watermark %j in a JSON box", value => {
    const html=renderToStaticMarkup(<AttemptBody record={{watermark:value}} index={0} />);
    const section=html.split('data-phase-section="watermark"')[1].split('</section>')[0];
    expect(jsonBoxText(section)).toBe(JSON.stringify(value));expect(section).toContain('aria-label="Copy JSON"');
    const absent=renderToStaticMarkup(<AttemptBody record={{}} index={0} diagnostics={[{field:"/attempts/0/watermark",reason:"omitted"}]} />);
    expect(absent.split('data-phase-section="watermark"')[1]).toContain('Watermark detail unavailable');
  });

  it("defaults Read & watermark to collapsed while retaining complete two-card first-level evidence", () => {
    const model=buildDataflowStory({read_request:{watermark_start:0,read_range:{column:"event_ts",start:false,end:null,"extra/key":[]},"custom/key":false},attempts:[{source:{selection:{lower:"old-bound"}},watermark:{saved_watermark:"old-saved"}},{attempt_number:2,source:{selection:{mode:"range",lower:0,upper:null}},watermark:{load_status:"loaded",candidate:{kind:"observed_max",values:false},saved_watermark:[],"extra/key":null}}],diagnostic_issues:[{field:"/read_request/missing~1key",reason:"omitted"},{field:"/attempts/1/watermark/candidate/values",reason:"invalid"}]});
    const closed=renderToStaticMarkup(<DataflowReadWatermark model={model} />);
    expect(closed).toContain('<summary>');expect(closed).not.toContain('open=""');expect(closed).not.toContain('dataflow-story-read-values');expect(closed).not.toContain('dataflow-section-block');expect(closed).toContain('omitted');expect(closed).toContain('invalid');
    const body=renderToStaticMarkup(<DataflowReadWatermarkBody model={model} />);
    expect(body.match(/class="dataflow-section-block /g)).toHaveLength(2);
    for(const path of ["/read_request/read_range","/read_request/custom~1key","/read_request/missing~1key","/attempts/1/source/selection","/attempts/1/watermark/candidate","/attempts/1/watermark/extra~1key"]) expect(body).toContain(`data-field-path="${path}"`);
    expect(body).not.toContain('data-field-path="/read_request/read_range/start"');expect(body).not.toContain('data-field-path="/attempts/1/watermark/candidate/values"');expect(body).not.toContain('old-bound');expect(body).not.toContain('old-saved');expect(body).not.toContain('View JSON');expect(body).not.toContain('>JSON</span>');expect(body).toContain('&quot;extra/key&quot;');
  });

  it("shows every native attempt and phase schema field in its section", () => {
    const defs=schema;
    const record:Record<string,unknown>=Object.fromEntries(Object.keys(defs.attempt.properties).map(key=>[key,key==="message" ? null : key==="status" ? "succeeded" : 0]));
    for(const phase of ["source","transform","destination","watermark"]){
      record[phase]=Object.fromEntries(Object.keys(defs[`${phase}Phase`].properties).map(key=>[key,key==="message" ? null : key==="selection" ? Object.fromEntries(Object.keys(defs.selection.properties).map(field=>[field,false])) : key==="candidate" ? {kind:"observed_max",values:[]} : key==="status" ? "succeeded" : 0]));
    }
    const html=renderToStaticMarkup(<AttemptBody record={record} index={3} />);
    for(const field of Object.keys(defs.attempt.properties).filter(key=>!["source","transform","destination","watermark"].includes(key))) expect(html,field).toContain(`data-field-path="/attempts/3/${field}"`);
    for(const phase of ["source","transform","destination"]){
      expect(html).toContain(`data-phase-section="${phase}"`);
      for(const field of Object.keys(defs[`${phase}Phase`].properties)) expect(html,`${phase}/${field}`).toContain(`data-field-path="/attempts/3/${phase}/${field}"`);
    }
    const watermarkSection=html.split('data-phase-section="watermark"')[1].split('</section>')[0];
    expect(JSON.parse(jsonBoxText(watermarkSection))).toEqual(record.watermark);
    expect(watermarkSection).toContain('data-field-path="/attempts/3/watermark"');
    for(const field of Object.keys(defs.selection.properties)) expect(html).toContain(`&quot;${field}&quot;`);
    for(const field of ["kind","values"]) expect(html).toContain(`&quot;${field}&quot;`);
    expect(html).not.toContain('data-field-path="/attempts/3/source/selection/lower"');
    expect(html).not.toContain('data-field-path="/attempts/3/watermark/candidate/kind"');
    expect(html).not.toContain("Authored read request");expect(html).not.toContain("View final read");
  });

  it("retains extra captured keys, exact escaped pointers and omitted nested evidence within the attempt", () => {
    const record={attempt_number:9,status:"failed",additional_context:false,source:{rows_read:null,source_action:null,selection:{mode:"range","a/b~c":0}},transform:{transformers_applied:[]},destination:{operation_details:null},watermark:{load_status:"loaded",save_status:"save_error",candidate:null,"custom/key":false}};
    const html=renderToStaticMarkup(<AttemptBody record={record} index={1} diagnostics={[{field:"/attempts/1/source/selection/missing~1key",reason:"omitted"},{field:"/attempts/1/producer_context",reason:"invalid"},{field:"/attempts/10/watermark/unrelated",reason:"truncated"}]} />);
    for(const path of ["additional_context","producer_context","source/source_action","source/selection","destination/operation_details","watermark"]) expect(html).toContain(`data-field-path="/attempts/1/${path}"`);
    expect(html).toContain('&quot;a/b~c&quot;');expect(html).toContain('/attempts/1/source/selection/missing~1key: omitted');
    expect(html).toContain("omitted");expect(html).toContain("invalid");expect(html).not.toContain("unrelated");expect(html).toContain(">null<");expect(html).toContain(">false<");
  });

  it("shows immediate JSON and value-only local message boxes with each phase's own status", () => {
    const sourceAction={query:"SELECT "+"payload_".repeat(800)};
    const html=renderToStaticMarkup(<AttemptBody record={{status:"succeeded",message:"attempt result",source:{status:"failed",message:"source failure",source_action:sourceAction,selection:{lower:0}},transform:{status:"skipped",message:"transform skip"},destination:{status:"running",message:"destination running"}}} index={0} />);
    const action=html.slice(html.indexOf('data-field-path="/attempts/0/source/source_action"'),html.indexOf('data-field-path="/attempts/0/source/selection"'));
    expect(action).toContain('<pre');expect(action).toContain("payload_".repeat(800));expect(action).not.toContain("View JSON");
    expect(action).not.toContain('>JSON</span>');expect(action).toContain('aria-label="Copy JSON"');
    for(const [path,color,value] of [["message","#f4fbf8","attempt result"],["source/message","#fff6f6","source failure"],["transform/message","#fffaf0","transform skip"],["destination/message","#f4f7ff","destination running"]]) {
      const box=html.slice(html.indexOf(`data-field-path="/attempts/0/${path}"`)).split('</dl>')[0];
      expect(box).toContain(`background:${color}`);expect(box).toContain(value);expect(box).not.toContain('<h4>');expect(box).not.toContain('<dt>Attempt');
    }
    expect(html).not.toContain('dataflow-attempt-nested');
  });

  it("shows phase duration as one labeled field and colors each attempt using its own status", () => {
    const record={attempt_number:2,status:"succeeded",message:"attempt end",source:{status:"failed",duration_seconds:0,message:"source end"},transform:{status:"skipped",duration_seconds:null},destination:{status:"succeeded",duration_seconds:3}};
    const html=renderToStaticMarkup(<AttemptBody record={record} index={0} />);
    for(const phase of ["source","transform","destination"]){
      expect(html.match(new RegExp(`data-field-path="/attempts/0/${phase}/duration_seconds"`,"g"))).toHaveLength(1);
      const section=html.slice(html.indexOf(`data-phase-section="${phase}"`)).split('</article>')[0];
      expect(section.split('</header>')[0]).not.toContain("duration_seconds");
      expect(section).toContain('<dt>Duration ');
    }
    const attempts=renderToStaticMarkup(<ExecutionAttempts model={buildDataflowStory({status:"failed",attempts:[{status:"failed"},record,{status:"pending"}]})} />);
    const styles=[...attempts.matchAll(/<details[^>]+>/g)].filter(match=>match[0].includes('dataflow-execution-attempt')).map(match=>match[0].match(/style="background:([^"]+)"/)?.[1]);
    expect(styles).toEqual(["#f8f5ff","#f4fbf8","#fff6f6"]);
  });

  it.each([null,false,0,"", "plain text"])("keeps scalar %j readable without a copy action", value => {
    const html=renderToStaticMarkup(<StoryExactValue value={value} text />);
    expect(html).not.toContain("<button");expect(html).not.toContain("Copy exact value");
    expect(html).toContain(value === "" ? "&quot;&quot;" : String(value));
  });

  it("defaults Configuration to an unmounted collapsed body and retains all recorded metadata when expanded", () => {
    const metadata=["dataflow_id","dataflow_name","workspace_id","dataflow_description","stage","group_number","execution_order","processing_mode","is_active","configure",...Object.keys(schema.dataflowRecord.properties).filter(field=>field.startsWith("source_")||field.startsWith("transform_")||field.startsWith("destination_")),"operation_type"];
    const row=Object.fromEntries(metadata.map((field,index)=>[field,[null,"",false,0][index%4]]));
    const closed=renderToStaticMarkup(<DataflowStoryConfiguration row={row} diagnostics={[{field:"/source_configure/params",reason:"omitted"}]} />);
    expect(closed).toContain('<summary>');expect(closed).not.toContain('open=""');expect(closed).not.toContain('dataflow-story-config-grid');expect(closed).not.toContain('data-field-path');expect(closed).toContain('omitted');
    const config=renderToStaticMarkup(<DataflowConfigurationBody row={row} />);
    expect(config).toContain('data-config-section="dataflow"');
    const phases=config.split('class="dataflow-story-config-phases"')[1];
    expect(phases).not.toContain('data-config-section="dataflow"');
    expect([...phases.matchAll(/data-config-section="([^"]+)"/g)].map(match=>match[1])).toEqual(['source','transform','destination']);
    for(const field of metadata) expect(config,field).toContain(`data-field-path="/${field}"`);
    for(const value of ["null","&quot;&quot;","false",">0<"]) expect(config).toContain(value);
    const absent=renderToStaticMarkup(<DataflowConfigurationBody row={{source_name:"recorded"}} />);
    expect(absent).not.toContain('data-field-path="/source_query"');
  });

  it("shows complete SQL and nested metadata directly in header-free boxes with their own copy action", () => {
    const sql="SELECT '<tag>' AS name\n-- preserved\nFROM orders WHERE id > 0";
    const configure={enabled:false,nested:{count:parseRawRecordJson('9007199254740993'),long:"x".repeat(1000)}};
    const html=renderToStaticMarkup(<DataflowConfigurationBody row={{source_query:sql,source_configure:configure,transform_select_columns:["id","name"]}} />);
    const query=html.split('data-field-path="/source_query"')[1].split('</dd>')[0];
    expect(jsonBoxText(query)).toBe(sql);expect(query).toContain('aria-label="Copy SQL"');expect(query).not.toContain('>SQL</span>');
    const config=html.split('data-field-path="/source_configure"')[1].split('</dd>')[0];
    expect(jsonBoxText(config)).toContain('9007199254740993');expect(jsonBoxText(config)).toContain('"enabled": false');expect(config).toContain("x".repeat(1000));expect(config).toContain('aria-label="Copy JSON"');expect(config).not.toContain('>JSON</span>');
    expect(html).not.toContain('<summary>');expect(html).not.toContain('data-field-path="/source_configure/nested"');
  });
  it("renders numbers distinctly from identical strings and keeps exact numeric tokens", () => {
    const number = renderToStaticMarkup(<StoryExactValue value={parseRawRecordJson("9007199254740993")} />);
    const string = renderToStaticMarkup(<StoryExactValue value="9007199254740993" />);
    expect(number).toContain(">9007199254740993</span>");
    expect(string).toContain("&quot;9007199254740993&quot;");
    expect(number).not.toContain("9k");
  });

  it.each([null,false,0,[],{}])("keeps meaningful native value %j", value => {
    expect(storyValuePreview(value).text).toBe(JSON.stringify(value));
    expect(storyValuePreview(value).complex).toBe(false);
  });

  it.each([["__date__","2026-01-01"],["__datetime__","2026-01-01T12:00:00+03:00"],["__time__","12:00:00"],["__decimal__","1.000"],["__binary__","AA=="]])("keeps tagged %s literal", (tag,value) => {
    const html=renderToStaticMarkup(<StoryExactValue value={{[tag]:value}} />);
    expect(html).toContain(value);
    expect(html).not.toContain("GMT+");
    expect(storyValuePreview({[tag]:value}).complex).toBe(false);
  });

  it("does not mistake arbitrary objects for a typed tag and defers large encoded content", () => {
    expect(storyValuePreview({__date__:"2026-01-01",extra:true}).text).toContain("extra");
    const encoded='{"secret_detail":"'+"x".repeat(1000)+'","nested":{"raw":false}}';
    const closed=renderToStaticMarkup(<StoryExactValue value={encoded} kind="json" />);
    expect(closed).not.toContain("x".repeat(500));
    expect(closed).not.toContain("<pre");
    const expanded=renderToStaticMarkup(<StoryExpandedValue value={encoded} kind="json" />);
    expect(expanded).toContain("x".repeat(1000));
    expect(expanded).toContain("nested");
    const hugeNumber=parseRawRecordJson("9".repeat(200));
    expect(storyValuePreview(hugeNumber).text).toBe("9".repeat(160)+"…");
    expect(renderToStaticMarkup(<StoryExactValue value={hugeNumber} />)).not.toContain("9".repeat(200));
    expect(renderToStaticMarkup(<StoryExpandedValue value={hugeNumber} />)).toContain("9".repeat(200));
  });

  it.each([[],{}, {enabled:false,number:1,text:"1"}, {__decimal__:"1.000"}])("puts short native JSON %j in a labeled box with one copy action", value => {
    const html=renderToStaticMarkup(<StoryExactValue value={value} />);
    expect(html).toContain("monitoring-code-box-json");
    expect(html).toContain("<pre");
    expect(html.match(/aria-label="Copy JSON"/g)).toHaveLength(1);
    expect(html).not.toContain("Copy exact value");
  });

  it("preserves SQL literals, comments, newlines and escaped markup in the syntax box", () => {
    const sql="SELECT 'from  a, and b', '<script>'\n-- where  retained\nFROM t WHERE x = 1";
    const html=renderToStaticMarkup(<StoryExpandedValue value={sql} kind="sql" />);
    const plain=html.match(/<pre[^>]*>([\s\S]*?)<\/pre>/)![1].replace(/<[^>]+>/g,"");
    expect(plain).toBe(sql.replace(/'/g,"&#x27;").replace(/</g,"&lt;").replace(/>/g,"&gt;"));
    expect(html).toContain("sql-comment");
    expect(html).not.toContain("<script>");
    expect(html.match(/aria-label="Copy SQL"/g)).toHaveLength(1);
  });

  it("shows recorded qualified table fields even when identical and keeps diagnostic attribution", () => {
    const row={source_table:"db.schema.table",source_full_table:"db.schema.table",destination_table:"table",destination_full_table:"db.schema.table"};
    const html=renderToStaticMarkup(<DataflowConfigurationBody row={row} />);
    expect(html).toContain('data-field-path="/source_full_table"');
    expect(html).toContain('data-field-path="/destination_full_table"');
    const flagged=renderToStaticMarkup(<DataflowConfigurationBody row={row} diagnostics={[{field:"/source_full_table",reason:"truncated"}]} />);
    expect(flagged).toContain('data-field-path="/source_full_table"');
    expect(flagged).toContain("truncated");
  });

  it("matches diagnostic component boundaries including escaped keys and attempt indices", () => {
    const diagnostics=[{field:"/attempts/10/source/selection/a~1b",reason:"invalid"},{field:"/attempts/1/source/selection2",reason:"truncated"},{field:"/attempts/1/source/selection/a~1b",reason:"omitted"}];
    expect(storyIssues(diagnostics,"/attempts/1/source/selection")).toEqual([diagnostics[2]]);
    expect(storyIssues(diagnostics,"/attempts/1/source/selection/a~1b")).toEqual([diagnostics[2]]);
  });

  it("attributes null/omitted root and historical messages to their own scope", () => {
    const model=buildDataflowStory({message:null,attempts:[{message:null},{message:"final-text"}],diagnostic_issues:[{field:"/message",reason:"omitted"},{field:"/attempts/0/message",reason:"invalid"}]});
    const root=renderToStaticMarkup(<DataflowDetailSections row={model.root} />);
    expect(root).toContain("Message omitted in log.");
    const old=renderToStaticMarkup(<AttemptBody record={model.attempts![0] as Record<string,unknown>} index={0} diagnostics={model.diagnostics} />);
    expect(old).toContain("Attempt: message invalid in log.");expect(old).not.toContain("final-text");expect(old).not.toContain("Run: message omitted");
  });

  it("keeps diagnostic attribution when configuration/read/phase values are absent", () => {
    const diagnostics=[{field:"/source_configure/params",reason:"omitted"},{field:"/read_request/read_range",reason:"invalid"},{field:"/attempts/0/destination",reason:"omitted"}];
    const config=renderToStaticMarkup(<DataflowConfigurationBody row={{source_configure:null}} diagnostics={diagnostics} />);
    expect(config).toContain("Configure");expect(config).toContain("omitted");expect(config).toContain("null");
    const read=renderToStaticMarkup(<DataflowReadWatermark model={buildDataflowStory({attempts:[{}],diagnostic_issues:diagnostics})} />);
    expect(read).toContain("Read &amp; watermark");expect(read).toContain("invalid");
    const phases=renderToStaticMarkup(<PhaseExecution record={{}} diagnostics={diagnostics} />);
    expect(phases).toContain("omitted");expect(phases).toContain("Phase detail unavailable");expect(phases.match(/No phase recorded/g)).toHaveLength(2);
  });

  it("preserves distinct historical selection/lifecycle without repeating root request", () => {
    const first={source:{status:"failed",selection:{lower:"old-bound",upper:null}},watermark:{load_status:"reused",loaded_watermark:"old-loaded",merge_base:null,candidate:{kind:"opaque",values:"old-candidate"},save_status:"save_error",saved_watermark:null}};
    const html=renderToStaticMarkup(<AttemptBody record={first} index={0} diagnostics={[{field:"/read_request/read_range",reason:"invalid"}]} />);
    expect(html).toContain("old-bound");expect(html).toContain("old-candidate");expect(html).toContain("save_error");
    const expanded=renderToStaticMarkup(<StoryReadValues selection={first.source.selection} watermark={first.watermark} attemptIndex={0} includeRequest={false} />);
    expect(expanded).toContain("old-bound");expect(expanded).toContain("old-candidate");
    expect(html).not.toContain("Authored read request");expect(html).not.toContain("new-bound");
  });

  it.each(["observed_max","request_end","opaque","metadata"])("keeps candidate %s kind separate from its exact value", kind => {
    const html=renderToStaticMarkup(<StoryReadValues includeRequest={false} attemptIndex={0} watermark={{load_status:"load_error",loaded_watermark:null,merge_base:0,candidate:{kind,values:false},watermark_to_save:[],save_status:"no_candidate",saved_watermark:{}}} />);
    for(const value of ["Candidate",kind,"false","load_error"]) expect(html).toContain(value);
    expect(html.match(/monitoring-code-box-json/g)).toHaveLength(3);
  });

  it("covers every root/fixed nested schema field with an intentional destination", () => {
    const technical=["log_schema_version","_type","datacoolie_version","log_session_id","job_id","job_num","job_index","dataflow_id","workspace_id","dataflow_run_id","source_id","destination_id","diagnostic_issues"];
    const runtime=["dataflow_name","stage","source_name","destination_name","source_format","destination_format","destination_load_type","operation_type","start_time","end_time","duration_seconds","status","message","retry_attempts","read_request","attempts","metrics"];
    const configuration=Object.keys(schema.dataflowRecord.properties).filter(field=>!technical.includes(field)&&!runtime.includes(field));
    const configured=renderToStaticMarkup(<DataflowConfigurationBody row={Object.fromEntries(configuration.map(field=>[field,`${field}-probe`]))} />);
    for(const field of configuration) expect(configured,field).toContain(`data-field-path="/${field}"`);
    expect(new Set([...technical,...runtime,...configuration]).size).toBe(70);
    expect(storyMetricFields).toEqual(Object.keys(schema.metrics.properties));
    expect(Object.keys(schema.attempt.properties)).toEqual(["attempt_number","start_time","end_time","duration_seconds","status","message","source","transform","destination","watermark"]);
    const bounds=renderToStaticMarkup(<StoryReadValues request={Object.fromEntries(Object.keys(schema.readRequest.properties).map(field=>[field,field==="read_range"?Object.fromEntries(Object.keys(schema.readRange.properties).map(key=>[key,1])):1]))} selection={Object.fromEntries(Object.keys(schema.selection.properties).map(field=>[field,1]))} watermark={Object.fromEntries(Object.keys(schema.watermarkPhase.properties).map(field=>[field,field==="candidate"?{kind:"metadata",values:1}:1]))} attemptIndex={0} />);
    for(const field of Object.keys(schema.readRequest.properties).filter(field=>field!=="read_range")) expect(bounds).toContain(`/read_request/${field}`);
    for(const field of Object.keys(schema.selection.properties)) expect(bounds).toContain(`&quot;${field}&quot;`);
    for(const field of Object.keys(schema.readRange.properties)) expect(bounds).toContain(`&quot;${field}&quot;`);
    for(const phase of ["source","transform","destination"]){
      const fields=Object.keys(schema[`${phase}Phase`].properties);
      const html=renderToStaticMarkup(<PhaseExecution record={{[phase]:Object.fromEntries(fields.map(field=>[field,field==="status"?"succeeded":field==="selection"?null:1]))}} />);
      for(const field of fields.filter(field=>!["status","duration_seconds","message","selection"].includes(field))) expect(html).toContain(`/attempts/0/${phase}/${field}`);
    }
  });

  it("flattens phase counters once and shows full root workload directly", () => {
    const html=renderToStaticMarkup(<PhaseExecution record={{source:{rows_read:0},destination:{rows_written:2,rows_inserted:1,files_added:0,bytes_saved:10}}} />);
    expect(html.match(/>Rows written /g)).toHaveLength(1);expect(html).not.toContain("Phase details");
    const drawer=renderToStaticMarkup(<DataflowDetailSections row={{metrics:{rows_read:700,rows_inserted:321},attempts:[{source:{rows_read:2}}]}} />);
    expect(drawer).not.toContain("Additional run metrics");expect(drawer).toContain(">321<");expect(drawer).toContain("Final");
  });
});
