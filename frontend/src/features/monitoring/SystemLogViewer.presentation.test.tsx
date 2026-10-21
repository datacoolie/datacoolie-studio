import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SystemLogViewer } from "./SystemLogViewer";

vi.mock("react-dom", () => ({ createPortal: (children: unknown) => children }));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...await importOriginal<typeof import("@tanstack/react-query")>(),
  useInfiniteQuery: () => ({
    data: { pages: [{ total: 1, files: [{ source_id: 2, file_uri: "other-job.jsonl" }], records: [{
      timestamp: "2026-10-09T10:00:00Z", level: "ERROR", logger: "test", msg: "Failed",
      job_id: "job-1", exc_info: "Traceback\nValueError: bad input",
    }], errors: [] }] },
    isPending: false, isFetching: false, isFetchingNextPage: false, hasNextPage: false,
  }),
}));

afterEach(() => vi.unstubAllGlobals());

describe("System log viewer presentation", () => {
  it("keeps a compact header, visible traceback and a whole-job original download", () => {
    vi.stubGlobal("document", { body: {} });
    const html = renderToStaticMarkup(<SystemLogViewer environmentId={1} jobId="job-1" sourceId={2} logSessionScope="exact" logSessionId="" timezoneName="UTC" onClose={() => {}} />);
    expect(html).toContain("Download job log");
    expect(html).toContain("system-logs/download?job_id=job-1&amp;source_id=2&amp;log_session_scope=exact&amp;log_session_id=");
    expect(html).toContain("system-log-inline-trace");
    expect(html).toContain("ValueError: bad input");
    expect(html).not.toContain("Full details");
    expect(html).not.toContain("Copy full loaded");
    expect(html).not.toContain("other-job.jsonl");
    expect(html.match(/class="system-log-summary-chip"/g)).toHaveLength(2);
  });
});
