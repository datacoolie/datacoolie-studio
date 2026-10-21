import { describe, expect, it } from "vitest";
import { buildDataflowStory, noStoryAttemptMessage, storyAttemptsDescending, storyMessages, storyMetric, storyOutcome } from "./dataflowStoryModel";

describe("Dataflow story evidence scopes", () => {
  it.each([[1,2,3],[7,2]])("reverses native chronology %j while preserving indices and final evidence", (...numbers) => {
    const attempts = numbers.map(attempt_number=>({attempt_number}));
    const model = buildDataflowStory({attempts});
    const display = storyAttemptsDescending(model);
    expect(display.map(value=>value.record?.attempt_number)).toEqual([...numbers].reverse());
    expect(display.map(value=>value.nativeIndex)).toEqual(numbers.map((_,i)=>i).reverse());
    expect(display[0].isFinal).toBe(true);
    expect(model.attempts).toEqual(attempts);
    expect(model.final).toBe(attempts.at(-1));
  });

  it("preserves unavailable attempts and returns no display records during loading", () => {
    const model = buildDataflowStory({attempts:[{attempt_number:1},null]});
    expect(storyAttemptsDescending(model)[0]).toEqual({record:null,nativeIndex:1,isFinal:true});
    expect(storyAttemptsDescending(buildDataflowStory({attempts:[{}]},"loading"))).toEqual([]);
  });
  it("keeps root totals distinct from final phases and earlier recovered attempts", () => {
    const model = buildDataflowStory({ raw_record_json: JSON.stringify({ status: "succeeded", duration_seconds: 30,
      metrics: { rows_read: 12, rows_written: 10 }, attempts: [
        { attempt_number: 7, status: "failed", source: { status: "failed", rows_read: 99 } },
        { attempt_number: 2, status: "succeeded", source: { status: "succeeded", rows_read: 5, duration_seconds: 0 }, destination: null },
      ] }) });
    expect(storyMetric(model, "rows_read")?.toString()).toBe("12");
    expect(model.final?.attempt_number?.toString()).toBe("2");
    expect(model.recovered).toBe(true);
    expect(model.longestPhases).toEqual(["source"]);
    expect(model.final?.destination).toBeNull();
  });

  it("does not let flat root counters or incomplete loading establish a phase", () => {
    const missing = buildDataflowStory({ destination_rows_written: 0, source_status: "succeeded" });
    expect(missing.final).toBeNull();
    expect(missing.attempts).toBeNull();
    const pending = buildDataflowStory({ attempts: [], status: "running" }, "loading");
    expect(pending.attempts).toBeNull();
    expect(noStoryAttemptMessage(pending)).toContain("unavailable");
    const empty = buildDataflowStory({ attempts: [], status: "failed" });
    expect(noStoryAttemptMessage(empty)).toContain("failed before pipeline entry");
  });

  it("compares recorded duration independently of phase_health and keeps ties/nulls", () => {
    const model = buildDataflowStory({ phase_health: "source", attempts: [{ source: { duration_seconds: null },
      transform: { duration_seconds: 2 }, destination: { duration_seconds: 2 } }] });
    expect(model.longestPhases).toEqual(["transform", "destination"]);
    expect(buildDataflowStory({ attempts: [{ source: {} }] }).longestPhases).toEqual([]);
  });

  it("groups identical text only within a scope with exact diagnostic path provenance", () => {
    const record = { message: "same", source: { message: "same" }, destination: { message: "different" } };
    const diagnostics = [{ field: "/attempts/1/source/message", reason: "truncated" }];
    const messages = storyMessages(record, record, 1, diagnostics);
    expect(messages).toEqual([
      { text: "same", labels: ["Attempt", "Source"], truncatedLabels: ["Source"], coverage: [{label:"Source",reason:"truncated"}] },
      { text: "different", labels: ["Destination"], truncatedLabels: [], coverage: [] },
    ]);
    expect(storyMessages(record, record, 0, diagnostics)[0].truncatedLabels).toEqual([]);
  });

  it("preserves lossless numbers and tagged watermark evidence", () => {
    const model = buildDataflowStory({ raw_record_json: '{"metrics":{"rows_read":9007199254740993},"attempts":[{"watermark":{"merge_base":null,"candidate":{"values":{"x":{"__decimal__":"1.000"}}},"save_status":"not_attempted","saved_watermark":null}}]}' });
    expect(storyMetric(model, "rows_read")?.toString()).toBe("9007199254740993");
    expect(model.final?.watermark).toEqual({ merge_base: null, candidate: { values: { x: { __decimal__: "1.000" } } }, save_status: "not_attempted", saved_watermark: null });
  });

  it("groups current attempt messages with run/phases while preserving each origin diagnostic", () => {
    const model=buildDataflowStory({message:"same",attempts:[{message:"old"},{message:"same",source:{message:"same"}}],
      diagnostic_issues:[{field:"/attempts/1/message",reason:"omitted"},{field:"/attempts/1/source/message",reason:"truncated"}]});
    expect(model.messages).toEqual([{text:"same",labels:["Run","Attempt","Source"],truncatedLabels:["Source"],
      coverage:[{label:"Attempt",reason:"omitted"},{label:"Source",reason:"truncated"}]}]);
  });
});

describe("Recorded run narrative", () => {
  it("keeps the failure phase distinct from longest duration and performance signal", () => {
    const model = buildDataflowStory({ status: "failed", performance_bottleneck_phase: "destination", attempts: [{
      source: { status: "failed", duration_seconds: 1 }, destination: { status: "succeeded", duration_seconds: 9 },
    }] });
    expect(storyOutcome(model)).toEqual({ headline: "Failed", detail: "Failure reported in source.", failurePhase: "source" });
    expect(model.longestPhases).toEqual(["destination"]);
  });

  it("does not infer failure or complete measurement from unequal observed counters", () => {
    const model = buildDataflowStory({ status: "succeeded", metrics: { rows_read: 42, rows_written: 40 }, attempts: [{ source: { status: "succeeded" }, destination: { status: "succeeded" } }] });
    expect(storyOutcome(model)).toEqual({ headline: "Succeeded", detail: "", failurePhase: null });
  });

  it("only reports all-phase success and retry recovery when native evidence supports it", () => {
    const success = { status: "succeeded", source: { status: "succeeded" }, transform: { status: "succeeded" }, destination: { status: "succeeded" } };
    expect(storyOutcome(buildDataflowStory({ status: "succeeded", attempts: [success] })).detail).toBe("All three phases reported success.");
    expect(storyOutcome(buildDataflowStory({ status: "succeeded", retry_attempts: 3, attempts: [success] })).headline).toBe("Succeeded");
    const row = { status: "succeeded", attempts: [{ status: "failed" }, success] };
    expect(storyOutcome(buildDataflowStory(row)).headline).toBe("Succeeded after retry");
    expect(storyOutcome(buildDataflowStory(row, "loading")).headline).toBe("Succeeded");
  });

  it.each(["running", "pending", "skipped", "failed"])("does not turn absent %s evidence into invented execution", status => {
    const model = buildDataflowStory({ status, attempts: [] }, "error");
    expect(storyOutcome(model).detail).not.toMatch(/no data|completed|pipeline entry/i);
    expect(model.final).toBeNull();
  });
});
