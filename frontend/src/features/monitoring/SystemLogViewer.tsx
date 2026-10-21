import { Copy, Download, ListTree, Search, WrapText, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { createPortal } from "react-dom";
import { monitoringSystemLogsOptions, systemLogDownloadParams, SYSTEM_LOG_PAGE_SIZE } from "./monitoringQueries";
import { api } from "../../shared/api/client";
import type { MonitoringSessionScope } from "../../shared/api/domainTypes";
import { CompactNumberValue } from "../../shared/components/CompactNumberValue";
import { Help } from "../../shared/components/Help";
import { dismissActiveHelp } from "../../shared/components/helpCoordinator";
import { resolveIntlTimezone } from "../../shared/time";
import { highlightJson } from "./details/detailPrimitives";
import {
  collectSystemLogReadErrors,
  displaySystemLogValue,
  displaySystemLogMessage,
  formatSystemLogRecord,
  logLevelTone,
  logRecordKey,
  readErrorSummary,
  sortLogRecords,
  systemLogParts,
  uniqueSystemLogFiles,
  type SystemLogPage,
} from "./systemLogModel";

export { systemLogScopeParams } from "./monitoringQueries";
export {
  collectSystemLogReadErrors,
  displaySystemLogValue,
  displaySystemLogMessage,
  formatFullSystemLogRecord,
  formatSystemLogRecord,
  logLevelTone,
  systemLogParts,
  systemLogDetailEntries,
  uniqueSystemLogFiles,
} from "./systemLogModel";
export type { SystemLogPage, SystemLogParts } from "./systemLogModel";

const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;
const EMPTY_LOG_PAGES: SystemLogPage[] = [];

interface SystemLogViewerProps {
  environmentId: number;
  jobId: string;
  jobLabel?: string | null;
  sourceId?: number;
  logSessionScope?: MonitoringSessionScope;
  logSessionId?: string | null;
  dataflowId?: string;
  dataflowLabel?: string | null;
  dataflowRunId?: string;
  eventName?: string;
  timezoneName?: string | null;
  onClose: () => void;
}

interface CopyFeedback {
  tone: "success" | "error";
  message: string;
}

type CopyHandler = (label: string, value: string) => void;

export function SystemLogViewer({
  environmentId,
  jobId,
  jobLabel,
  sourceId,
  logSessionScope,
  logSessionId,
  dataflowId,
  dataflowLabel,
  dataflowRunId,
  eventName,
  timezoneName,
  onClose,
}: SystemLogViewerProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const feedbackTimerRef = useRef<number | null>(null);
  const onCloseRef = useRef(onClose);
  const [searchValue, setSearchValue] = useState("");
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState("");
  const [includeDataflowLogs, setIncludeDataflowLogs] = useState(false);
  const [wrapped, setWrapped] = useState(true);
  const [copyFeedback, setCopyFeedback] = useState<CopyFeedback | null>(null);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  const logsQuery = useInfiniteQuery(monitoringSystemLogsOptions(environmentId, {
    jobId,
    sourceId,
    logSessionScope,
    logSessionId,
    dataflowId,
    dataflowRunId,
    eventName,
    includeDataflowLogs,
    level,
    query,
  }));

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(searchValue.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [searchValue]);

  useEffect(() => {
    dismissActiveHelp();
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusFrame = window.requestAnimationFrame(() => searchRef.current?.focus());

    function handleKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented) return;
      if (event.key === "Escape") {
        if (dismissActiveHelp(event)) return;
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = [...dialogRef.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
      )].filter((element) => {
        if (element.hidden) return false;
        const closedDetails = element.closest("details:not([open])");
        if (closedDetails && !closedDetails.querySelector(":scope > summary")?.isSameNode(element)) return false;
        return element.getClientRects().length > 0;
      });
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown, true);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.removeEventListener("keydown", handleKeyDown, true);
      document.body.style.overflow = previousOverflow;
      window.requestAnimationFrame(() => {
        previousFocus?.setAttribute("data-help-restoring-focus", "true");
        previousFocus?.focus();
        queueMicrotask(() => previousFocus?.removeAttribute("data-help-restoring-focus"));
      });
    };
  }, []);

  useEffect(() => () => {
    if (feedbackTimerRef.current !== null) window.clearTimeout(feedbackTimerRef.current);
  }, []);

  const pages = logsQuery.data?.pages ?? EMPTY_LOG_PAGES;
  const records = useMemo(() => sortLogRecords(pages.flatMap((page) => page.records)), [pages]);
  const total = pages.at(-1)?.total ?? 0;
  const files = useMemo(() => uniqueSystemLogFiles(pages.flatMap((page) => page.files)), [pages]);
  const readErrors = useMemo(() => collectSystemLogReadErrors(pages), [pages]);
  const loaded = records.length;
  const responseLoaded = Boolean(logsQuery.data);
  const loading = logsQuery.isPending;
  const loadingMore = logsQuery.isFetchingNextPage;
  const refreshing = logsQuery.isFetching && !loading && !loadingMore;
  const error = logsQuery.error instanceof Error ? logsQuery.error.message : logsQuery.error ? String(logsQuery.error) : null;
  const nextPageError = Boolean(logsQuery.isFetchNextPageError);
  const canLoadMore = Boolean(logsQuery.hasNextPage && !logsQuery.isFetchingNextPage && !error);
  const resolvedTimezone = resolveIntlTimezone(timezoneName);
  const viewerTitle = presentLabel(dataflowLabel) && dataflowId
    ? dataflowLabel as string
    : presentLabel(jobLabel) ?? jobId;
  const scopeMode = dataflowRunId
    ? `Dataflow run ${dataflowRunId}`
    : dataflowId
      ? `Dataflow ${displayScopeIdentity(dataflowLabel, dataflowId)}`
      : includeDataflowLogs
        ? "Job + dataflows"
        : "Job only";
  const compactLoadedText = useMemo(
    () => records.map((record) => formatSystemLogRecord(record, timezoneName)).join("\n"),
    [records, timezoneName],
  );
  const downloadUrl = api.monitoringSystemLogDownloadUrl(environmentId, systemLogDownloadParams({
    jobId, sourceId, logSessionScope, logSessionId,
  }));

  function showCopyFeedback(feedback: CopyFeedback) {
    setCopyFeedback(feedback);
    if (feedbackTimerRef.current !== null) window.clearTimeout(feedbackTimerRef.current);
    feedbackTimerRef.current = window.setTimeout(() => setCopyFeedback(null), 2200);
  }

  async function copyText(label: string, text: string) {
    if (!text) {
      showCopyFeedback({ tone: "error", message: `No ${label.toLowerCase()} is available.` });
      return;
    }
    try {
      if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(text);
      showCopyFeedback({ tone: "success", message: `${label} copied.` });
    } catch {
      showCopyFeedback({ tone: "error", message: `Could not copy ${label.toLowerCase()}.` });
    }
  }

  function loadMore() {
    if (canLoadMore) void logsQuery.fetchNextPage();
  }

  const scopeEntries = [
    `Job: ${displayScopeIdentity(jobLabel, jobId)}`,
    `Source: ${sourceId === undefined ? "all" : sourceId}`,
    `Session: ${logSessionScope === "null" ? "NULL" : logSessionScope === "exact" ? `exact ${JSON.stringify(logSessionId ?? "")}` : "all"}`,
    `Dataflow: ${dataflowId ? displayScopeIdentity(dataflowLabel, dataflowId) : "all"}`,
    ...(dataflowRunId ? [`Run: ${dataflowRunId}`] : []),
    ...(eventName ? [`Event: ${eventName}`] : []),
    `Scope: ${scopeMode}`,
    `Timezone: ${resolvedTimezone}`,
  ];

  return createPortal(
    <div
      className="system-log-dialog-backdrop"
      onMouseDown={(event) => {
        event.stopPropagation();
        onClose();
      }}
    >
      <section
        ref={dialogRef}
        className="system-log-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="system-log-dialog-header">
          <Help as="div" className="system-log-dialog-title" title={scopeEntries.join("\n")}>
            <span>System log</span>
            <div className="system-log-heading">
              <h2 id={titleId}>{viewerTitle}</h2>
            </div>
          </Help>
          <div className="system-log-dialog-summary" aria-label="System log counts and timezone">
            <span className="system-log-summary-chip">{loading ? "Loading records" : <><CompactNumberValue compactStyle="infix" value={loaded} /> / <CompactNumberValue compactStyle="infix" value={total} /> loaded</>}</span>
            <Help className="system-log-summary-chip" title="Display timezone">{resolvedTimezone}</Help>
          </div>
          <Help as="button" className="icon-action" type="button" aria-label="Close system logs" title="Close" onClick={onClose}>
            <X size={17} />
          </Help>
        </header>

        <div className="system-log-toolbar">
          <label className="system-log-search">
            <Search size={14} aria-hidden="true" />
            <input
              ref={searchRef}
              type="search"
              value={searchValue}
              placeholder="Search messages, logger, event…"
              aria-label="Search system logs"
              onChange={(event) => setSearchValue(event.target.value)}
            />
          </label>
          <label className="system-log-level-filter">
            <select value={level} aria-label="Filter log level" onChange={(event) => setLevel(event.target.value)}>
              <option value="">All levels</option>
              {LOG_LEVELS.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          {!dataflowId ? (
            <Help as="button"
              className={`system-log-tool${includeDataflowLogs ? " is-active" : ""}`}
              type="button"
              aria-pressed={includeDataflowLogs}
              title="Include log records emitted by child dataflows in this job"
              onClick={() => setIncludeDataflowLogs((value) => !value)}
            >
              <ListTree size={14} /> Include dataflow logs
            </Help>
          ) : null}
          <button className={`system-log-tool${wrapped ? " is-active" : ""}`} type="button" aria-pressed={wrapped} onClick={() => setWrapped((value) => !value)}>
            <WrapText size={14} /> Wrap
          </button>
          <button className="system-log-tool" type="button" disabled={!records.length} onClick={() => void copyText("Loaded logs", compactLoadedText)}>
            <Copy size={14} /> Copy loaded
          </button>
          <Help as="a" className="system-log-tool" href={downloadUrl} download target="_blank" rel="noopener noreferrer" title="Download original JSONL for this job, including child dataflows. Search and level filters do not affect the download.">
            <Download size={14} /> Download job log
          </Help>
          {dataflowId ? <Help className="system-log-scope-label" title={scopeMode}>{dataflowRunId ? "Dataflow run" : "Dataflow"}</Help> : null}
          {refreshing ? <span className="system-log-refresh-status">Refreshing…</span> : null}
          {copyFeedback ? <span className={`system-log-copy-feedback is-${copyFeedback.tone}`} role="status" aria-live="polite">{copyFeedback.message}</span> : null}
        </div>

        <div className={`system-log-viewer${wrapped ? " is-wrapped" : ""}`} aria-live="polite" aria-busy={loading}>
          {loading ? <SystemLogState title="Loading system logs…" detail="Reading the indexed log files for this scope." /> : null}
          {!loading && error && !nextPageError && !records.length ? (
            <SystemLogState tone="error" title="Unable to read system logs" detail={error} onRetry={() => void logsQuery.refetch()} />
          ) : null}
          {!loading && error && nextPageError && !records.length ? (
            <SystemLogState tone="error" title="Unable to load the next log page" detail={error} onRetry={() => void logsQuery.fetchNextPage()} />
          ) : null}
          {!loading && responseLoaded && !records.length && !error && !files.length && !readErrors.length ? (
            <SystemLogState title="No indexed system logs" detail="No indexed files are available for this source and session scope." />
          ) : null}
          {!loading && responseLoaded && !records.length && !error && readErrors.length ? (
            <SystemLogState
              tone="error"
              title="Indexed log files could not be read"
              detail="No records were loaded. Review the file diagnostics below, then retry the read."
              onRetry={() => void logsQuery.refetch()}
            />
          ) : null}
          {!loading && responseLoaded && !records.length && !error && !readErrors.length && files.length > 0 ? (
            <SystemLogState
              title="No matching log records"
              detail={query || level
                ? "Clear the search or level filter to see more records."
                : !dataflowId && !includeDataflowLogs
                  ? "No job-level records were found. Enable Include dataflow logs to inspect child dataflows."
                  : "The indexed files contain no records for this scope."}
            />
          ) : null}
          {records.length ? (
            <div className="system-log-lines" role="log" aria-label="System log records">
              {records.map((record, index) => (
                <SystemLogLine
                  key={logRecordKey(record, index)}
                  record={record}
                  timezoneName={timezoneName}
                  onCopy={copyText}
                />
              ))}
              {error ? (
                <div className="system-log-read-warning is-error">
                  {nextPageError ? `Could not load more records: ${error}` : `Could not refresh system logs: ${error}`}
                  <button type="button" onClick={() => void (nextPageError ? logsQuery.fetchNextPage() : logsQuery.refetch())}>{nextPageError ? "Retry load more" : "Retry read"}</button>
                </div>
              ) : null}
              {canLoadMore ? (
                <button className="system-log-load-more" type="button" disabled={loadingMore} onClick={loadMore}>
                  {loadingMore ? "Loading…" : `Load ${SYSTEM_LOG_PAGE_SIZE} more`}
                </button>
              ) : null}
            </div>
          ) : null}
          {readErrors.length ? <SystemLogReadDiagnostics errors={readErrors} onRetry={() => void logsQuery.refetch()} /> : null}
        </div>
      </section>
    </div>,
    document.body,
  );
}

function SystemLogLine({
  record,
  timezoneName,
  onCopy,
}: {
  record: Record<string, unknown>;
  timezoneName?: string | null;
  onCopy: CopyHandler;
}) {
  const parts = systemLogParts(record, timezoneName);
  return (
    <article className="system-log-line">
      <div className="system-log-primary">
        <span className="system-log-time">{parts.time}</span>
        <span className={`system-log-level is-${logLevelTone(parts.level)}`}>[{displaySystemLogValue(parts.level)}]</span>
        <span className="system-log-logger">{displaySystemLogValue(parts.logger)}</span>
        {parts.dataflowId ? (
          <Help as="button" className="system-log-copy-id system-log-dataflow" type="button" title="Copy dataflow ID" aria-label="Copy dataflow ID" onClick={() => onCopy("Dataflow ID", parts.dataflowId as string)}>
            [{displaySystemLogValue(parts.dataflowId)}] <Copy size={11} aria-hidden="true" />
          </Help>
        ) : null}
        {parts.eventName ? <span className="system-log-event">[{displaySystemLogValue(parts.eventName)}]</span> : null}
        <span className="system-log-message-separator" aria-hidden="true"> - </span>
        <span className="system-log-message">{displaySystemLogMessage(parts.message)}</span>
      </div>
      {parts.excInfo ? <pre className="system-log-inline-trace">{parts.excInfo}</pre> : null}
    </article>
  );
}

function SystemLogState({
  title,
  detail,
  tone = "neutral",
  onRetry,
}: {
  title: string;
  detail: string;
  tone?: "neutral" | "error";
  onRetry?: () => void;
}) {
  return (
    <div className={`system-log-state is-${tone}`}>
      <strong>{title}</strong>
      <span>{detail}</span>
      {onRetry ? <button className="system-log-load-more" type="button" onClick={onRetry}>Retry</button> : null}
    </div>
  );
}

function SystemLogReadDiagnostics({ errors, onRetry }: { errors: Array<Record<string, unknown>>; onRetry: () => void }) {
  return (
    <section className="system-log-diagnostics" aria-label="File read diagnostics">
      <div className="system-log-diagnostics-header">
        <strong>File read diagnostics</strong>
        <span><CompactNumberValue compactStyle="infix" value={errors.length} /> warning{errors.length === 1 ? "" : "s"}</span>
        <button className="system-log-tool" type="button" onClick={onRetry}>Retry read</button>
      </div>
      <div className="system-log-diagnostics-list">
        {errors.map((error, index) => (
          <details className="system-log-diagnostic" key={`${readErrorSummary(error)}-${index}`}>
            <summary>{readErrorSummary(error)}</summary>
            <pre>{highlightJson(JSON.stringify(error, null, 2) ?? String(error))}</pre>
          </details>
        ))}
      </div>
    </section>
  );
}

function presentLabel(value?: string | null) {
  return value && value.trim() ? value : null;
}

function displayScopeIdentity(label: string | null | undefined, id: string) {
  const displayLabel = presentLabel(label);
  return displayLabel && displayLabel !== id ? `${displayLabel} (${id})` : id;
}
