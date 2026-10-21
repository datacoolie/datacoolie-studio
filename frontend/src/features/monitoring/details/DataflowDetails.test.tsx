import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DataflowDetailSections, DiagnosticDetails, DataflowTechnicalBody } from "./DataflowStoryDetails";
import { AttemptBody, PhaseExecution } from "./DataflowAttemptExecution";
import { DataflowRunSummary, DataflowSummaryInsight, summaryPhaseTiming } from "./DataflowRunSummary";
import { DataflowReadWatermark, DataflowReadWatermarkBody, DataflowWatermarkSummary } from "./DataflowStoryWatermark";
import { DataflowConfigurationBody } from "./DataflowStoryConfiguration";
import { buildDataflowStory, storyMessages } from "./dataflowStoryModel";
import { StoryDisclosure, StoryJson, StoryMessages, StoryDuration, StoryBytes, StoryCard } from "./DataflowStoryPrimitives";
import { JSON_BLOCK_FIELDS } from "./detailPrimitives";
import { DataflowMessageSection } from "./DataflowMessages";
import { WatermarkBadge } from "../components/monitoringPresentation";

describe("dataflow transform details", () => {
  it("renders direct transform attributes and keeps configure values grouped", () => {
    const html = renderToStaticMarkup(<DataflowConfigurationBody row={{
      transform_select_columns: '["id", "email"]',
      transform_drop_columns: "[]",
      transform_rename_columns: '{"email": "contact_email"}',
      transform_value_rules: '[{"operation": "trim", "columns": ["email"]}]',
      transform_hash_columns: '[{"target_column": "email_hash", "columns": ["email"]}]',
      transform_masking_rules: '[{"method": "redact", "columns": ["email"], "value": "[PRIVATE]"}]',
      transform_missing_column_policy: "ignore",
      transform_configure: '{"missing_column_policy": "ignore"}',
    }} />);

    for (const label of [
      "Select columns",
      "Drop columns",
      "Rename columns",
      "Value rules",
      "Hash columns",
      "Masking rules",
      "Configure",
    ]) {
      expect(html).toContain(label);
    }
    expect(html).not.toContain("Missing column policy");
    expect(html).toContain("missing_column_policy");
    expect(html).toContain("contact_email");
    expect(html).toContain("[PRIVATE]");
    expect(html).not.toContain("View JSON");
    expect(html).not.toContain('>JSON</span>');
    expect(html).toContain("ignore");
  });

  it("formats expanded transform collections as JSON blocks", () => {
    for (const field of [
      "transform_select_columns",
      "transform_drop_columns",
      "transform_rename_columns",
      "transform_value_rules",
      "transform_hash_columns",
      "transform_masking_rules",
    ]) {
      expect(JSON_BLOCK_FIELDS.has(field)).toBe(true);
    }
  });
  it("groups canonical messages separately from execution and phase metadata", () => {
    const html = renderToStaticMarkup(<DataflowDetailSections row={{
      status: "succeeded", message: "completed successfully", attempts: [{source: { message: "read complete" }, transform: { message: "transform complete" }, destination: {message: "write complete"}}],
      log_session_id: "SESSION-A", datacoolie_version: "0.2.0",
      log_schema_version: 4,
    }} />);
    for (const text of ["completed successfully", "read complete", "transform complete", "write complete",
      "Run summary", "Final attempt"]) {
      expect(html).toContain(text);
    }
    for (const text of ["Watermark kind", "Start operator", "End operator"]) {
      expect(html).not.toContain(text);
    }
    const messageSection = html.match(/<section class="dataflow-section-block dataflow-summary-message"[\s\S]*?<\/section>/)?.[0];
    expect(messageSection).toContain("<h4>Message</h4>");
    for (const text of ["completed successfully"]) {
      expect(messageSection).toContain(text);
      expect(html.split(text)).toHaveLength(2);
    }
    expect(html).not.toContain("Master data");
    expect(html).not.toContain("Runtime info");
    expect(html).not.toContain("SESSION-A");
    expect(html).not.toContain("Log schema version");
  });

  it("preserves multiline messages as escaped text and marks only reported truncation", () => {
    const html = renderToStaticMarkup(<DataflowMessageSection row={{
      message: "first line\n<script>alert('x')</script>", message_truncated: true,
      source_message: "read complete", source_message_truncated: false,
    }} />);
    expect(html).toContain("first line\n&lt;script&gt;");
    expect(html).not.toContain("<script>");
    expect(html.split("Message truncated in log.")).toHaveLength(2);
  });

  it("hides absent messages without adding empty phase placeholders", () => {
    expect(renderToStaticMarkup(<DataflowMessageSection row={{ message: " ", source_message: null }} />)).toBe("");
    const html = renderToStaticMarkup(<DataflowMessageSection row={{ message_truncated: true }} />);
    expect(html).toContain("Message truncated in log.");
    expect(html).not.toContain("<dt>Source</dt>");
  });

  it("keeps retry messages scoped while deferring history bodies", () => {
    const attempts = [
      { attempt_number: 1, status: "failed", message: "retry outcome", source: { message: "retry read failed" } },
      { attempt_number: 2, status: "succeeded", message: "last outcome", destination: { message: "last write complete" } },
    ];
    const row = { message: "final outcome", attempts };
    const html = renderToStaticMarkup(<DataflowDetailSections row={row} />);
    expect(html).toContain("Latest attempt first");
    expect(html).toContain("last outcome");
    expect(html).toContain("last write complete");
    const current = renderToStaticMarkup(<StoryMessages messages={buildDataflowStory(row).messages} />);
    expect(current).toContain("last outcome");
    expect(current).toContain("last write complete");
    expect(html).not.toContain("retry outcome");
    expect(html).not.toContain("retry read failed");
    const first = renderToStaticMarkup(<StoryMessages messages={storyMessages(attempts[0], attempts[0], 0, null)} attempt />);
    expect(first).toContain("retry read failed");
    expect(first).not.toContain("last write complete");
  });

  it("renders native v6 attempts and keeps request, selection, candidate, and save evidence distinct", () => {
    const rawRecord = '{"metrics":{"rows_read":9007199254740993,"authored_text":"9007199254740993"},"read_request":{"watermark_start":1,"watermark_end":2,"watermark_start_operator":">=","watermark_end_operator":"<","read_range":null,"reference_time":null},"attempts":[{"attempt_number":1,"status":"succeeded","source":{"selection":{"lower":1,"upper":2,"upper_origin":"request"},"source_action":{"mode":"read"}},"transform":{"status":"succeeded"},"destination":{"status":"succeeded"},"watermark":{"loaded_watermark":{"value":"2026-01-01"},"merge_base":{"value":"2025-12-01"},"candidate":{"values":{"value":"2026-01-02"},"kind":"typed"},"watermark_to_save":{"value":"2026-01-02"},"save_status":"saved","saved_watermark":{"value":"2026-01-02"}}}],"diagnostic_issues":[{"field":"/attempts/0/source/source_action","reason":"omitted"}]}';
    const html = renderToStaticMarkup(<DataflowDetailSections row={{ raw_record_json: rawRecord }} />);
    expect(html).toContain("Read &amp; watermark");
    expect(html).not.toContain("Previous attempts");
    expect(html).toContain("Technical details");
    expect(html).not.toContain("9007199254740993");
    expect(html).toContain('&quot;candidate&quot;');
    const model = buildDataflowStory({ raw_record_json: rawRecord });
    const evidence = renderToStaticMarkup(<StoryJson value={model.native} copyPayload={model.payload} />);
    expect(evidence).toContain("9007199254740993");
    expect(evidence).toContain("upper_origin");
    expect(html).not.toContain("No attempts recorded");
  });

  it("explains empty attempt history from the terminal status", () => {
    const skipped = renderToStaticMarkup(<DataflowDetailSections row={{ status: "skipped", raw_record_json: '{"attempts":[]}' }} />);
    const failed = renderToStaticMarkup(<DataflowDetailSections row={{ status: "failed", raw_record_json: '{"attempts":[]}' }} />);
    expect(skipped).toContain("The run was skipped before pipeline entry");
    expect(failed).toContain("The run failed before pipeline entry");
  });

  it.each(["saved", "no_candidate", "not_requested", "not_attempted", "save_error"])("keeps %s persistence distinct from candidate and saved state", (saveStatus) => {
    const raw = {read_request: {watermark_start:{x:{__decimal__:"1.000"}},watermark_end:null,watermark_start_operator:">=",watermark_end_operator:"<"},
      attempts:[{source:{selection:{lower:{x:1},upper:{x:2},combine_operator:"OR",upper_origin:"request"}},watermark:{load_status:"loaded",loaded_watermark:{x:1},merge_base:null,candidate:{values:{x:2},kind:"typed"},watermark_to_save:{x:2},save_status:saveStatus,saved_watermark:saveStatus==="saved"?{x:2}:null}}]};
    const model = buildDataflowStory({raw_record_json:JSON.stringify(raw)});
    const html = renderToStaticMarkup(<DataflowReadWatermarkBody model={model} />);
    const summary = renderToStaticMarkup(<DataflowReadWatermark model={model} />);
    expect(summary).not.toContain(saveStatus);
    expect(html).toContain(saveStatus);
    for (const text of ["Selection","Merge base","Candidate","Watermark to save","Saved watermark","1.000","upper_origin","OR"]) expect(html).toContain(text);
    expect(html).toContain("null");
  });

  it("keeps unchanged values exact and reports the omitted issue sentinel without changing status", () => {
    const model = buildDataflowStory({status:"succeeded",attempts:[{watermark:{load_status:"loaded",merge_base:{x:{__date__:"2026-01-01"}},saved_watermark:{x:{__date__:"2026-01-01"}},save_status:"saved"}}],diagnostic_issues:[{field:"/diagnostic_issues",reason:"omitted"}]});
    const values = renderToStaticMarkup(<DataflowReadWatermarkBody model={model} />);
    for (const key of ["merge_base", "saved_watermark"]) {
      const field=values.split(`data-field-path="/attempts/0/watermark/${key}"`)[1].split('</dd>')[0];
      expect(field.replace(/<[^>]+>/g," ")).toMatch(/&quot;__date__&quot;\s*:\s*&quot;2026-01-01&quot;/);
    }
    const diagnostics = renderToStaticMarkup(<DiagnosticDetails model={model} />);
    expect(diagnostics).toContain("Additional issue count is unknown");
    expect(model.root.status).toBe("succeeded");
  });

  it("renders native lossless duration and byte units with exact evidence", () => {
    const model = buildDataflowStory({ raw_record_json: '{"duration_seconds":0.169966,"metrics":{"bytes_added":9007199254740993}}' });
    const html = renderToStaticMarkup(<><StoryDuration value={model.root.duration_seconds} /><StoryBytes value={model.metrics?.bytes_added} /></>);
    expect(html).toContain("0.17s");
    expect(html).toContain("0.169966 seconds");
    expect(html).toContain("9,007,199,254,740,993 B");
    expect(html).toContain("TB");
  });

  it("renders absent phases without root metric aliases or synthetic zero execution", () => {
    const html = renderToStaticMarkup(<PhaseExecution record={{source: {status: null, duration_seconds: 0, rows_read: 0}, destination: null}} />);
    expect(html.match(/No phase recorded/g)).toHaveLength(2);
    expect(html).toContain("Unknown");
    expect(html).toContain("0.00s");
    expect(html).not.toContain("Rows written");
  });

  it("normalizes native phase and attempt naive End timestamps with explicit UTC Start", () => {
    const start = "2026-07-22T01:02:03Z";
    const end = "2026-07-22T01:02:03";
    const card = renderToStaticMarkup(<StoryCard title="Attempt timing" runtime timezoneName="Asia/Saigon" rows={[["Start",start,"start_time"],["End",end,"end_time"]]} />);
    expect(card.match(/08:02:03/g)).toHaveLength(2);
    const phase = renderToStaticMarkup(<PhaseExecution record={{source:{start_time:start,end_time:end,status:"succeeded"}}} timezoneName="Asia/Saigon" />);
    expect(phase.match(/08:02:03/g)).toHaveLength(2);
  });

  it("prioritizes cause and keeps heavy disclosures unmounted while showing loading/error", () => {
    const failed = renderToStaticMarkup(<DataflowDetailSections row={{status: "failed", failure_category: "Throttle", failure_phase: "source", attempts: []}} />);
    expect(failed.indexOf("Throttle")).toBeLessThan(failed.indexOf("Technical details"));
    expect(failed.indexOf("Throttle")).toBeLessThan(failed.indexOf("Run summary"));
    expect(failed).not.toContain("Execution JSON");
    const pending = renderToStaticMarkup(<DataflowDetailSections row={{attempts: []}} availability="loading" />);
    expect(pending).toContain("Loading execution details");
    expect(pending).not.toContain("before pipeline entry");
    const error = renderToStaticMarkup(<DataflowDetailSections row={{}} availability="error" onRetry={() => {}} />);
    expect(error).toContain("Retry");
  });

  it("keeps final attempt status, timing and message accessible without a second final body", () => {
    const html=renderToStaticMarkup(<DataflowDetailSections row={{status:"running",start_time:"root-start",end_time:null,
      attempts:[{attempt_number:7,status:"failed",message:"old failure"},{attempt_number:2,status:"pending",start_time:"attempt-start",end_time:null,message:"current attempt"}]}} />);
    expect(html).toContain("Latest attempt first");
    expect(html).toContain("attempt-start");
    expect(html).toContain("status-pending");
    expect(html.split("current attempt")).toHaveLength(2);
    expect(html).not.toContain("old failure");
    expect(html.match(/dataflow-story-attempt-summary/g)).toHaveLength(2);
    const single=renderToStaticMarkup(<DataflowDetailSections row={{start_time:"same-start",end_time:"same-end",attempts:[{start_time:"same-start",end_time:"same-end"}]}} />);
    expect(single).not.toContain("Previous attempts");
    expect(single).not.toContain("<span>Time ");
  });

  it("keeps signed byte differences exact when the source value exceeds safe integer range", () => {
    const html = renderToStaticMarkup(<DataflowDetailSections row={{
      destination_bytes_added: "9007199254740993",
      destination_bytes_removed: "1",
    }} />);
    expect(html).toContain("9,007,199,254,740,992 B");
  });

});

describe("Run overview and evidence disclosures", () => {
  it.each(["succeeded","failed","skipped","running","pending","unknown"])("uses recorded contribution for %s regardless of diagnostic phase health", status => {
    const model=buildDataflowStory({status,phase_health:"unknown",performance_bottleneck_phase:"unknown",overhead_duration_seconds:null,duration_seconds:999,attempts:[{status,duration_seconds:10,source:{status,duration_seconds:2},transform:{status,duration_seconds:1},destination:{status,duration_seconds:5}}]});
    const timing=summaryPhaseTiming(model);
    expect(timing.proportional).toBe(true);expect(timing.unavailable).toBe(false);expect(timing.phases.map(phase=>phase.duration)).toEqual([2,1,5,2]);expect(timing.phases.map(phase=>phase.share)).toEqual([20,10,50,20]);
    const html=renderToStaticMarkup(<DataflowSummaryInsight model={model} />);expect(html).toContain('dataflow-insight-phase-bar');expect(html).toContain('name-destination">Destination');expect(html).not.toContain("Phase timing unavailable.");
  });

  it("derives contribution overhead from final native duration with absent phases consuming no time", () => {
    const timing=(attempt:Record<string,unknown>)=>summaryPhaseTiming(buildDataflowStory({status:"skipped",phase_health:"unknown",overhead_duration_seconds:500,duration_seconds:999,attempts:[{duration_seconds:99,source:{duration_seconds:99}},attempt]}));
    expect(timing({duration_seconds:5,source:{duration_seconds:2}}).phases.map(phase=>phase.duration)).toEqual([2,null,null,3]);
    expect(timing({duration_seconds:5,source:{duration_seconds:2},transform:{duration_seconds:null}}).phases.map(phase=>phase.duration)).toEqual([2,null,null,null]);
    expect(timing({source:{duration_seconds:2}}).phases[3].duration).toBeNull();
    expect(timing({duration_seconds:1,source:{duration_seconds:2}}).phases[3].duration).toBe(0);
    expect(summaryPhaseTiming(buildDataflowStory({status:"skipped",attempts:[],overhead_duration_seconds:500})).proportional).toBe(false);
  });
  it("derives tied largest contributions when bottleneck is missing or unknown, without diagnosing from all-zero timing", () => {
    for(const reported of [undefined,"unknown"]){
      const model=buildDataflowStory({performance_bottleneck_phase:reported,overhead_duration_seconds:0,attempts:[{source:{duration_seconds:4},transform:{duration_seconds:1},destination:{duration_seconds:4}}]});
      const html=renderToStaticMarkup(<DataflowSummaryInsight model={model} />);
      expect(html).toContain('name-source">Source');expect(html).toContain('name-destination">Destination');expect(html).toContain("Largest contribution");expect(html).toContain('dataflow-insight-phase-bar');
    }
    const zero=renderToStaticMarkup(<DataflowSummaryInsight model={buildDataflowStory({overhead_duration_seconds:0,attempts:[{source:{duration_seconds:0},transform:{duration_seconds:0},destination:{duration_seconds:0}}]})} />);
     expect(zero).toContain('>unk</span></dd>');
    const reported=renderToStaticMarkup(<DataflowSummaryInsight model={buildDataflowStory({performance_bottleneck_phase:"transform",overhead_duration_seconds:0,attempts:[{source:{duration_seconds:20},transform:{duration_seconds:1},destination:{duration_seconds:2}}]})} />);
    expect(reported).toContain('name-transform">Transform');expect(reported).toContain("Reported performance bottleneck");
  });

  it("pairs workload and final Load/Save without losing zero, exact metrics or native field pointers", () => {
    const model=buildDataflowStory({metrics:{rows_read:"9007199254740993",rows_written:0,rows_inserted:1,rows_updated:2,rows_deleted:3,files_added:4,files_removed:5,bytes_added:6,bytes_removed:7,bytes_saved:8},attempts:[{watermark:{load_status:"loaded",save_status:"save_error"}}]});
    const html=renderToStaticMarkup(<DataflowRunSummary model={model} />);
    expect(html).toContain("9,007,199,254,740,993");expect(html).toContain("dataflow-summary-pair");
    for(const field of ["rows_read","rows_written","rows_inserted","rows_updated","rows_deleted","files_added","files_removed","bytes_added","bytes_removed","bytes_saved"])expect(html).toContain(`data-field-path="/metrics/${field}"`);
    for(const field of ["load_status","save_status"])expect(html).toContain(`data-field-path="/attempts/0/watermark/${field}"`);
    expect(html).toContain("action-inserted");expect(html).toContain("action-removed");expect(html).toContain("save_error");
  });

  it("renders a single status-colored root Message with no Run box or attempt substitution", () => {
    const html=renderToStaticMarkup(<DataflowRunSummary model={buildDataflowStory({status:"failed",message:"root total message",attempts:[{message:"latest attempt message"}]})} />);
    expect(html).toContain('background:#fff6f6');expect(html).toContain('<h4>Message</h4>');expect(html).toContain("root total message");
    expect(html).not.toContain('<dt>Run</dt>');expect(html).not.toContain('monitoring-dataflow-messages');expect(html).not.toContain("latest attempt message");
    const absent=renderToStaticMarkup(<DataflowRunSummary model={buildDataflowStory({message:null,attempts:[{message:"latest attempt message"}]})} />);
    expect(absent).not.toContain('dataflow-summary-message');
    expect(html).not.toContain('Copy Run ID');
    expect(renderToStaticMarkup(<DataflowRunSummary model={buildDataflowStory({dataflow_run_id:"exact-id"})} />)).toContain('aria-label="Copy Run ID"');
  });

  it("shows root status and exact Run ID in the quick summary", () => {
    const html=renderToStaticMarkup(<DataflowRunSummary model={buildDataflowStory({status:"failed",dataflow_run_id:"run-9007199254740993",attempts:[{status:"succeeded"}]})} />);
    expect(html).toContain("status-failed");expect(html).toContain("Run ID");expect(html).toContain("run-9007199254740993");
    expect(renderToStaticMarkup(<DataflowRunSummary model={buildDataflowStory({})} />)).toMatch(/<dt>Run ID<\/dt><dd>.*?unk/);
  });

  it("shortens long Run IDs in the middle while keeping the full value for inspection and copy", () => {
    const id="12345678-middle-of-long-run-id-87654321";
    const html=renderToStaticMarkup(<DataflowRunSummary model={buildDataflowStory({dataflow_run_id:id})} />);
    expect(html).toContain(`aria-description="${id}`);
    expect(html).toContain('>1234567-...-654321</button>');
    expect(html).toContain('aria-label="Copy Run ID"');
    expect(renderToStaticMarkup(<DataflowRunSummary model={buildDataflowStory({dataflow_run_id:"short-id"})} />)).toContain('>short-id</button>');
    expect(renderToStaticMarkup(<DataflowRunSummary model={buildDataflowStory({dataflow_run_id:"abc-def-xyz"})} />)).toContain('>abc-def-xyz</button>');
    const uuid="12345678-1234-5678-abcd-123456789abc";
    expect(renderToStaticMarkup(<DataflowRunSummary model={buildDataflowStory({dataflow_run_id:uuid})} />)).toContain('>1234567-...-789abc</button>');
  });

  it("formats compact phase legend with one decimal, grouped values and time units", () => {
    const html=renderToStaticMarkup(<DataflowSummaryInsight model={buildDataflowStory({performance_bottleneck_phase:"destination",overhead_duration_seconds:106660800,attempts:[{duration_seconds:106666349.9,source:{duration_seconds:59.9},transform:{duration_seconds:90},destination:{duration_seconds:5400}}]})} />);
    for(const text of ['aria-label="Source">S','aria-label="Transform">T','aria-label="Destination">D','aria-label="Overhead">O','59.9 s','1.5 min','1.5 h','1,234.5 d',"Contribution"])expect(html).toContain(text);
    expect(html).toContain('name-destination">Destination');
    expect(html).not.toContain("Destination ·");
  });

  it("uses final measured phase time including overhead, independently of retry-inclusive total", () => {
    const model=buildDataflowStory({duration_seconds:1000,overhead_duration_seconds:2,performance_bottleneck_phase:"destination",attempts:[{attempt_number:7,source:{duration_seconds:99}},{attempt_number:2,duration_seconds:10,source:{duration_seconds:2},transform:{duration_seconds:0},destination:{duration_seconds:6}}]});
    const timing=summaryPhaseTiming(model);
    expect(timing.measured).toBe(4);expect(timing.total).toBe(10);
    expect(timing.phases.map(phase=>phase.share)).toEqual([20,0,60,20]);
    const html=renderToStaticMarkup(<DataflowSummaryInsight model={model} />);
    expect(html).toContain(">Destination<");expect(html).toContain("0.0%");expect(html).not.toContain("99.00s");
  });

  it("labels partial timing and absent phases without inventing zero or performance bottleneck from failure health", () => {
    const model=buildDataflowStory({status:"failed",phase_health:"source_failed",attempts:[{source:{status:"failed",duration_seconds:0},destination:{duration_seconds:4}}]});
    const timing=summaryPhaseTiming(model);
    expect(timing.measured).toBe(2);expect(timing.phases.map(phase=>phase.share)).toEqual([0,null,100,null]);
    const html=renderToStaticMarkup(<DataflowSummaryInsight model={model} />);
    expect(html).not.toContain("Partial timing ·");expect(html).toContain("No phase recorded");expect(html).not.toContain("Longest recorded phase");
    expect(html).toContain('name-destination">Destination');expect(html).toContain("partial timing");expect(html).toContain("dataflow-insight-failure");expect(html).toContain("Failure phase");expect(html).toContain('name-source">Source');
  });

  it.each(["zero","missing","invalid"])("does not fabricate a proportional bar for %s evidence", kind => {
    const model=buildDataflowStory({phase_health:"unknown",overhead_duration_seconds:kind==="invalid"?-1:0,attempts:[{source:{duration_seconds:kind==="zero"?0:kind==="invalid"?false:null},transform:{duration_seconds:0},destination:{duration_seconds:0}}]});
    const timing=summaryPhaseTiming(model);
    expect(timing.proportional).toBe(false);
    expect(renderToStaticMarkup(<DataflowSummaryInsight model={model} />)).not.toContain('class="dataflow-insight-phase-bar"');
    if(kind==="invalid")expect(timing.phases[0].duration).toBeNull();
  });

  it("keeps read mode/final watermark and reported bottleneck separate from failure, without historic substitution", () => {
    const model=buildDataflowStory({status:"failed",performance_bottleneck_phase:"destination",movement_state:"unchanged",overhead_duration_seconds:0,attempts:[{source:{selection:{mode:"full"}},watermark:{save_status:"save_error"}},{source:{status:"failed",duration_seconds:1,selection:{mode:"incremental"}},transform:{duration_seconds:0},destination:{duration_seconds:3},watermark:{load_status:"loaded",save_status:"saved"}}]});
    const html=renderToStaticMarkup(<DataflowSummaryInsight model={model} />);
    for(const text of [">Destination<","Failure phase","source","incremental","Unchanged","loaded","saved"])expect(html).toContain(text);
    expect(html).not.toContain("save_error");expect(html).not.toContain('>full<');
    expect(html).toContain('/attempts/1/source/selection/mode');
  });

  it("keeps root Message in its separate bounded box and performance rule out of drawer sections", () => {
    const model=buildDataflowStory({message:"root-message",performance_candidate_code:"RULE_SENTINEL",performance_candidate_priority:7,attempts:[{message:"attempt-message"}]});
    const html=renderToStaticMarkup(<DataflowRunSummary model={model} />);
    expect(html).toContain('dataflow-summary-three-columns');expect(html).toContain('dataflow-summary-message');
    expect(html).toContain("root-message");expect(html).not.toContain("attempt-message");expect(html).not.toContain("RULE_SENTINEL");
    expect(renderToStaticMarkup(<DataflowTechnicalBody model={model} row={model.root} />)).not.toContain("RULE_SENTINEL");
    expect(renderToStaticMarkup(<DataflowRunSummary model={buildDataflowStory({})} />)).not.toContain('dataflow-summary-message');
    const truncated=renderToStaticMarkup(<DataflowRunSummary model={buildDataflowStory({message:null,diagnostic_issues:[{field:"/message",reason:"truncated"}]})} />);
    expect(truncated).toContain('dataflow-summary-message');expect(truncated).toContain("Message truncated in log.");
    const long=renderToStaticMarkup(<DataflowRunSummary model={buildDataflowStory({message:"x".repeat(10000)})} />);
    expect(long).toContain("x".repeat(1000));expect(long).toContain("monitoring-message-scroll");expect(long).not.toContain("Read full message");
  });
  it("shows summary metrics and insights directly without generated outcome prose or secondary disclosures", () => {
    const html=renderToStaticMarkup(<DataflowDetailSections row={{status:"failed",duration_seconds:0.5,failure_phase:"source",failure_category:"Other",source_status:"failed",source_duration_seconds:0.4,metrics:{rows_inserted:321,files_removed:0},performance_candidate_reason:"slow destination",attempts:[{source:{status:"failed"},destination:{duration_seconds:7}}]}} />);
    for(const text of ["Run summary","Run workload","Insight","Contribution","slow destination",">321<","Total duration","0.50s"]) expect(html).toContain(text);
    for(const text of ["Failed</strong>","Failure reported in","Other","Additional run metrics","Table phase details","Performance details"]) expect(html).not.toContain(text);
    expect(html.indexOf('data-field-path="/metrics/rows_inserted"')).toBeLessThan(html.indexOf('dataflow-story-execution'));
  });

  it("separates Read from Watermark while preserving the final diagnostic pointers", () => {
    const html=renderToStaticMarkup(<DataflowReadWatermarkBody model={buildDataflowStory({read_request:{watermark_start:0},attempts:[{}, {source:{selection:{lower:false}},watermark:{load_status:"loaded",save_status:"saved",loaded_watermark:null}}]})} />);
    expect(html).toContain('class="dataflow-section-block dataflow-read-region"');expect(html).toContain('class="dataflow-section-block dataflow-watermark-region"');
    expect(html.indexOf('dataflow-read-region')).toBeLessThan(html.indexOf('dataflow-watermark-region'));
    for(const path of ["/read_request/watermark_start","/attempts/1/source/selection","/attempts/1/watermark/loaded_watermark"]) expect(html).toContain(path);
    expect(html).not.toContain("Copy exact value");
  });
  it("renders all attempts descending with only final open and diagnostics at native paths", () => {
    const attempts = Array.from({length:40},(_,i)=>({attempt_number:i+1,status:i===39?"succeeded":"failed",source:{rows_read:i,message:`source-${i}`}}));
    const html=renderToStaticMarkup(<DataflowDetailSections row={{attempts,diagnostic_issues:[{field:"/attempts/39/source/rows_read",reason:"invalid"}]}} />);
    expect(html.match(/dataflow-story-attempt-summary/g)).toHaveLength(40);
    expect(html.match(/<details open=""/g)).toHaveLength(1);
    expect(html).toContain('data-attempt-body="39"');
    expect(html).not.toContain('data-attempt-body="0"');
    expect(html.indexOf('data-attempt-index="39"')).toBeLessThan(html.indexOf('data-attempt-index="38"'));
    expect(html).toContain('data-field-path="/attempts/39/source/rows_read"');
    expect(html).toContain("invalid");expect(html).toContain("source-39");expect(html).not.toContain("source-0");
  });

  it("keeps default disclosure closed and mounts a requested default-open body immediately", () => {
    const closed=renderToStaticMarkup(<StoryDisclosure title="Closed">{()=>"lazy-sentinel"}</StoryDisclosure>);
    const open=renderToStaticMarkup(<StoryDisclosure title="Open" defaultOpen>{()=>"mounted-sentinel"}</StoryDisclosure>);
    expect(closed).not.toContain("lazy-sentinel");expect(open).toContain('open=""');expect(open).toContain("mounted-sentinel");
  });

  it("orders route before summary and execution and includes final watermark within its attempt", () => {
    const row={attempts:[{attempt_number:1,source:{selection:{lower:7}},watermark:{load_status:"loaded",save_status:"saved",saved_watermark:"final-saved-value"}}]};
    const html=renderToStaticMarkup(<DataflowDetailSections row={row} />);
    expect(html.indexOf('dataflow-run-route')).toBeLessThan(html.indexOf('dataflow-story-summary'));
    expect(html.indexOf('dataflow-story-summary')).toBeLessThan(html.indexOf('dataflow-story-execution'));
    expect(html.indexOf('dataflow-story-execution')).toBeLessThan(html.indexOf('id="dataflow-read-watermark"'));
    expect(html).not.toContain("dataflow-run-route-transform");
    expect(html.split("final-saved-value")).toHaveLength(2);
    const attempt=renderToStaticMarkup(<AttemptBody record={row.attempts[0]} index={0} />);
    expect(attempt).toContain("final-saved-value");expect(attempt).toContain('data-field-path="/attempts/0/source/selection"');
    for(const text of ["Dataflow","Source","Transform","Destination","Watermark"]) expect(attempt).toContain(text);
  });

  it("shows repeated messages locally with only their own diagnostic and truncation origins", () => {
    const html=renderToStaticMarkup(<DataflowDetailSections row={{status:"succeeded",message:"same text",attempts:[{message:"same text",source:{message:"same text"}}],diagnostic_issues:[{field:"/attempts/0/source/message",reason:"truncated"}]}} />);
    expect(html.split("same text")).toHaveLength(4);expect(html).toContain("Source: message truncated in log.");expect(html).not.toContain("see run summary");expect(html).not.toContain('href="#dataflow-summary-message"');
  });
  it.each(["unchanged", "advanced", "initialized", "changed", "empty", "unknown", "not_configured"])("shows projected %s movement separately from a successful save", movement => {
    const model = buildDataflowStory({ movement_state: movement, attempts: [{ watermark: { load_status: "loaded", save_status: "saved" } }] });
    const html = renderToStaticMarkup(<DataflowWatermarkSummary model={model} />);
    expect(html.toLowerCase()).toContain(movement.replace(/_/g, " "));
    expect(html).toContain("Load: loaded");
    expect(html).toContain("Save: saved");
  });

  it("shows Empty as a neutral watermark result", () => {
    const html = renderToStaticMarkup(<WatermarkBadge value="empty" />);
    expect(html).toContain("badge-neutral");
    expect(html).toContain(">Empty</span>");
    expect(html).toContain('aria-description="Incremental read returned no rows; no watermark was persisted."');
  });

  it.each([undefined, null, "", " "])("keeps missing movement %s unknown when native evidence is absent", movement => {
    const html = renderToStaticMarkup(<DataflowDetailSections row={{ status: "skipped", attempts: [], movement_state: movement }} />);
    expect(html).toContain("Read &amp; watermark");
    expect(html.toLowerCase()).not.toContain("not configured");
    expect(html).toContain('data-field-path="/attempts/-1/watermark/save_status"');
  });

  it.each(["save_error", "not_attempted", "no_candidate", "not_requested"])("keeps native save state %s independent of movement", save => {
    const model = buildDataflowStory({ movement_state: "unknown", attempts: [{ watermark: { load_status: "not_requested", save_status: save } }] });
    const html = renderToStaticMarkup(<DataflowWatermarkSummary model={model} />);
    expect(html).toContain(`Save: ${save}`);
    expect(html).toContain("Load: not_requested");
    expect(html).toContain("Unknown");
  });

  it("keeps heavy final-phase fields unmounted with status, duration and diagnostic coverage visible", () => {
    const record = { source: { status: "failed", duration_seconds: 0, rows_read: 7, source_action: { secretFixtureMarker: "bounded evidence" } } };
    const html = renderToStaticMarkup(<PhaseExecution record={record} collapsible diagnostics={[{ field: "/attempts/0/source/source_action", reason: "omitted" }]} />);
    expect(html).toContain("status-failed");
    expect(html).toContain("0.00s");
    expect(html).toContain("omitted");
    expect(html).not.toContain("Rows read");
    expect(html).not.toContain("secretFixtureMarker");
    const expanded = renderToStaticMarkup(<PhaseExecution record={record} />);
    expect(expanded).toContain("Rows read");
    expect(expanded).toContain("secretFixtureMarker");
  });

  it("keeps omitted phase detail distinct from a confirmed absent phase", () => {
    const html = renderToStaticMarkup(<PhaseExecution record={{ source: null, transform: null, destination: null }} collapsible diagnostics={[{ field: "/attempts/0/source", reason: "omitted" }]} />);
    expect(html).toContain("Phase detail unavailable");
    expect(html.match(/No phase recorded/g)).toHaveLength(2);
  });

  it("labels context and keeps zero workload visible without deriving data loss", () => {
    const html = renderToStaticMarkup(<DataflowDetailSections row={{ status: "succeeded", duration_seconds: 0, stage: "bronze", operation_type: "etl", destination_load_type: "merge", metrics: { rows_read: 0, rows_written: null }, attempts: [] }} />);
    for (const text of ["Stage", "bronze", "Operation", "etl", "Load type", "merge", "0.00s", "Read", "Written", "unk"]) expect(html).toContain(text);
    expect(html).not.toMatch(/data loss|All three phases reported success/);
  });

  it("labels active duration as reported and keeps unknown total duration explicit", () => {
    const running = renderToStaticMarkup(<DataflowDetailSections row={{ status: "running", duration_seconds: 13 }} />);
    expect(running).toContain("Reported duration");
    expect(running).not.toContain("Running</strong> in");
    const pending = renderToStaticMarkup(<DataflowDetailSections row={{ status: "pending", duration_seconds: null }} />);
    expect(pending).toContain("Reported duration");expect(pending).toContain("unk");
  });

  it("removes phase projection and technical performance while retaining configuration context", () => {
    const row = { phase_health: "source_bottleneck", source_status: "failed", source_duration_seconds: 0, transform_duration_seconds: null, destination_operation_type: "merge", group_number: 0, execution_order: 7 };
    const phases = renderToStaticMarkup(<DataflowTechnicalBody model={buildDataflowStory(row)} row={row} />);
    expect(phases).not.toContain("Table phase projection");expect(phases).not.toContain("source_bottleneck");expect(phases).not.toContain("Technical performance evidence");expect(phases).toContain("unk");
    const config = renderToStaticMarkup(<DataflowConfigurationBody row={row} />);
    for (const text of ["Destination operation", "merge", "Group number", "Execution order", ">0<", ">7<"]) expect(config).toContain(text);
  });

  it("makes identity values clickable for copy and excludes source/destination identities", () => {
    const row={dataflow_run_id:"run-id",dataflow_id:"flow-id",job_id:"job-id",log_session_id:"session-id",_source_id:0,workspace_id:null,job_num:0,job_index:1,_type:"dataflow_run_log",source_id:"source-only",destination_id:"destination-only",datacoolie_version:"0.2.0",log_schema_version:7,_file_uri:"C:/log/file.json",_record_line:0,performance_candidate_code:"rule",performance_candidate_priority:0,performance_rows_processed:0,performance_overhead_ratio:0,performance_dominant_phase_ratio:0,diagnostic_issues:[{field:"/attempts/0/source/selection",reason:"omitted"}]};
    const html=renderToStaticMarkup(<DataflowTechnicalBody model={buildDataflowStory(row)} row={row} />);
    for(const label of ["Run ID","Dataflow ID","Job ID","Session ID","Log source ID","Workspace ID","Job number","Job index","Record type","DataCoolie version","Log schema version","File","Record line","/attempts/0/source/selection"])expect(html).toContain(`aria-label="Copy ${label}"`);
    const identity=html.split('dataflow-story-diagnostics')[0];expect(identity).not.toContain('<svg');expect(identity).toContain('dataflow-value-copy-button');expect(identity).toContain('>run-id</button>');expect(identity).toContain('>C:/log/file.json</button>');
    expect(html).not.toContain("Source entity ID");expect(html).not.toContain("Destination entity ID");expect(html).not.toContain("source-only");expect(html).not.toContain("destination-only");expect(html).not.toContain("Table phase projection");expect(html).toContain('data-field-path="/workspace_id"');expect(html).toContain(">null</button>");expect(html).toContain("Execution JSON");expect(html).not.toContain('<strong>');
    const closed=renderToStaticMarkup(<DataflowDetailSections row={row} />);
    expect(closed).not.toContain('dataflow-technical-identity');expect(closed).not.toContain('aria-label="Copy File"');
  });
});
