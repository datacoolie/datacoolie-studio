import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { StatusCell } from "../../shared/components/DataTable";
import type { MonitoringRecord } from "../../shared/api/domainTypes";
import { IssuePreview } from "./components/monitoringPresentation";
import { GroupedDetailCard, IssueCell, semanticText } from "./details/detailPrimitives";
import { effectiveMonitoringMessage, monitoringMessageState } from "./monitoringMessage";

describe("monitoring canonical messages", () => {
  it.each(["failed", "succeeded", "skipped", "running", "pending", "matched", "mismatch"])("keeps arbitrary message and ID text %s neutral", (value) => {
    expect(semanticText("message", value)).toEqual({ kind: "text", value });
    expect(semanticText("source_message", value)).toEqual({ kind: "text", value });
    expect(semanticText(undefined, value)).toEqual({ kind: "text", value });
    const html = renderToStaticMarkup(<GroupedDetailCard title="Execution"
      rows={[["Status", "succeeded", "status"], ["Message", value, "message"]]} />);
    expect(html).toContain(`is-neutral\">${value}</span>`);
    expect(html).toContain("is-success");
  });

  it.each(["status", "source_status", "transform_status", "destination_status", "latest_run_status"])("styles %s by the attribute", (field) => {
    expect(semanticText(field, "failed")).toEqual({ kind: "status", value: "failed" });
  });
  it("treats serialized canonical null as authoritative and only permits legacy fallback when presence is false", () => {
    expect(monitoringMessageState({ message: null, error_message: "stale failure" })).toMatchObject({
      value: null,
      source: "canonical",
      canonicalPresent: true,
    });
    expect(effectiveMonitoringMessage({ message: null, message_present: false, error_message: "legacy failure" }))
      .toBe("legacy failure");
    expect(effectiveMonitoringMessage({ message: "", error_message: "stale failure" })).toBeNull();
    expect(effectiveMonitoringMessage({ message: null, message_present: true, error_message: "stale failure" })).toBeNull();
    expect(effectiveMonitoringMessage({ message: null, message_present: true, source_message: "source explanation" })).toBeNull();
    expect(effectiveMonitoringMessage({ status: "succeeded", message: null, message_present: false, error_message: "stale failure" })).toBeNull();
    expect(effectiveMonitoringMessage({ status: "failed", message_present: false, error_message: "root error", error_preview: "root er..." })).toBe("root error");
    expect(effectiveMonitoringMessage({ status: "failed", message_present: false, source_error_message: "phase error" })).toBeNull();
  });

  it.each([
    ["succeeded", "Pipeline complete"],
    ["skipped", "No rows matched"],
    ["running", "Pipeline started"],
    ["pending", "Waiting for source"],
  ])("shows informational text for %s independently of the status badge", (status, message) => {
    const html = renderToStaticMarkup(<>
      <StatusCell row={{ status, child_failed_count: 4, message }} />
      <IssuePreview row={{ status, child_failed_count: 4, message }} />
    </>);
    expect(html).toContain(`status-${status}`);
    expect(html).toContain(message);
    expect(html).not.toContain("monitor-muted");
  });

  it("keeps phase messages in phase evidence instead of substituting the root message", () => {
    const html = renderToStaticMarkup(<IssueCell row={{
      status: "succeeded",
      source_message: "source complete",
      error_message: "stale failure",
    } as MonitoringRecord} />);
    expect(html).not.toContain("source complete");
    expect(html).not.toContain("stale failure");
    expect(html).toContain("monitor-muted");
  });

  it("shows the root canonical message independently of phase text", () => {
    const html = renderToStaticMarkup(<IssueCell row={{
      status: "failed", message: "pipeline failure", source_message: "source failure",
    } as MonitoringRecord} />);
    expect(html).toContain("pipeline failure");
    expect(html).not.toContain("source failure");
  });
});
