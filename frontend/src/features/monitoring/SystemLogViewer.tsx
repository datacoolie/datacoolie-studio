import { Check, Copy, ListTree, Search, WrapText, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { createPortal } from "react-dom";
import { formatTimestampForDisplay, parseTimestamp } from "../../shared/time";
import { monitoringSystemLogsOptions, SYSTEM_LOG_PAGE_SIZE } from "./monitoringQueries";
import { CompactNumberValue } from "./CompactNumberValue";

export { systemLogScopeParams } from "./monitoringQueries";

const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;

interface SystemLogViewerProps {
  environmentId: number;
  jobId: string;
  dataflowId?: string;
  timezoneName?: string | null;
  onClose: () => void;
}

export interface SystemLogParts {
  time: string;
  level: string;
  logger: string;
  func: string;
  line: string;
  dataflowId: string;
  message: string;
}

export function SystemLogViewer({ environmentId, jobId, dataflowId, timezoneName, onClose }: SystemLogViewerProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const [searchValue, setSearchValue] = useState("");
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState("");
  const [includeDataflowLogs, setIncludeDataflowLogs] = useState(false);
  const [wrapped, setWrapped] = useState(true);
  const [copied, setCopied] = useState(false);
  const logsQuery = useInfiniteQuery(monitoringSystemLogsOptions(environmentId, {
    jobId,
    dataflowId,
    includeDataflowLogs,
    level,
    query,
  }));

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(searchValue.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [searchValue]);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusFrame = window.requestAnimationFrame(() => searchRef.current?.focus());

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = [...dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
      )].filter((element) => !element.hidden);
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
      window.requestAnimationFrame(() => previousFocus?.focus());
    };
  }, [onClose]);

  const pages = logsQuery.data?.pages ?? [];
  const records = useMemo(() => sortLogRecords(pages.flatMap((page) => page.records)), [pages]);
  const total = pages.at(-1)?.total ?? 0;
  const files = useMemo(() => Array.from(new Set(pages.flatMap((page) => page.files))), [pages]);
  const readErrors = useMemo(() => pages.flatMap((page) => page.errors), [pages]);
  const loading = logsQuery.isPending;
  const loadingMore = logsQuery.isFetchingNextPage;
  const error = logsQuery.error instanceof Error ? logsQuery.error.message : logsQuery.error ? String(logsQuery.error) : null;
  const canLoadMore = Boolean(logsQuery.hasNextPage && !logsQuery.isFetchingNextPage);
  const scopeLabel = dataflowId ? `Dataflow ${dataflowId}` : includeDataflowLogs ? "Job + dataflows" : "Job only";

  function loadMore() {
    if (canLoadMore) void logsQuery.fetchNextPage();
  }

  async function copyVisibleLogs() {
    const text = records.map((record) => formatSystemLogRecord(record, timezoneName)).join("\n");
    if (!text) return;
    await navigator.clipboard.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }

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
          <div className="system-log-dialog-title">
            <span>Job system log</span>
            <h2 id={titleId}>{jobId}</h2>
          </div>
          <div className="system-log-dialog-summary">
            <span>{scopeLabel}</span>
            <span>{loading ? "Loading…" : <><CompactNumberValue value={total} /> records</>}</span>
            <span><CompactNumberValue value={files.length} /> files</span>
          </div>
          <button className="icon-action" type="button" aria-label="Close system logs" title="Close" onClick={onClose}>
            <X size={17} />
          </button>
        </header>

        <div className="system-log-toolbar">
          <label className="system-log-search">
            <Search size={14} aria-hidden="true" />
            <input
              ref={searchRef}
              type="search"
              value={searchValue}
              placeholder="Search messages, logger, function…"
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
            <button
              className={`system-log-tool${includeDataflowLogs ? " is-active" : ""}`}
              type="button"
              aria-pressed={includeDataflowLogs}
              title="Include log records emitted by child dataflows in this job"
              onClick={() => setIncludeDataflowLogs((value) => !value)}
            >
              <ListTree size={14} /> Include dataflow logs
            </button>
          ) : null}
          <button className={`system-log-tool${wrapped ? " is-active" : ""}`} type="button" aria-pressed={wrapped} onClick={() => setWrapped((value) => !value)}>
            <WrapText size={14} /> Wrap
          </button>
          <button className="system-log-tool" type="button" disabled={!records.length} onClick={() => void copyVisibleLogs()}>
            {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? "Copied" : "Copy loaded"}
          </button>
        </div>

        <div className={`system-log-viewer${wrapped ? " is-wrapped" : ""}`} aria-live="polite" aria-busy={loading}>
          {loading ? <SystemLogState title="Loading system logs…" detail="Reading the indexed log files for this job." /> : null}
          {!loading && error && !records.length ? <SystemLogState tone="error" title="Unable to read system logs" detail={error} /> : null}
          {!loading && !error && logsQuery.data && !files.length ? (
            <SystemLogState title="No indexed system logs" detail="Sync the configured log source, then open this viewer again." />
          ) : null}
          {!loading && files.length > 0 && !records.length ? (
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
              {records.map((record, index) => <SystemLogLine key={logRecordKey(record, index)} record={record} timezoneName={timezoneName} />)}
              {readErrors.length ? <div className="system-log-read-warning"><CompactNumberValue value={readErrors.length} /> file read warnings occurred while loading these logs.</div> : null}
              {error ? <div className="system-log-read-warning is-error">Could not load more records: {error}</div> : null}
              {canLoadMore ? (
                <button className="system-log-load-more" type="button" disabled={loadingMore} onClick={() => void loadMore()}>
                  {loadingMore ? "Loading…" : `Load ${SYSTEM_LOG_PAGE_SIZE} more`}
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      </section>
    </div>,
    document.body,
  );
}

function SystemLogLine({ record, timezoneName }: { record: Record<string, unknown>; timezoneName?: string | null }) {
  const parts = systemLogParts(record, timezoneName);
  return (
    <div className="system-log-line">
      <span className="system-log-time">{parts.time}</span>
      <LogSeparator />
      <span className={`system-log-level is-${logLevelTone(parts.level)}`}>[{parts.level}]</span>
      <LogSeparator />
      <span className="system-log-callsite">
        <span className="system-log-logger">{parts.logger}</span>
        <span className="system-log-punctuation">:</span>
        <span className="system-log-function">{parts.func}</span>
        <span className="system-log-punctuation">:</span>
        <span className="system-log-line-number">{parts.line}</span>
      </span>
      <LogSeparator />
      <span className={`system-log-dataflow${parts.dataflowId === "-" ? " is-empty" : ""}`}>[{parts.dataflowId}]</span>
      <LogSeparator />
      <span className="system-log-message">{parts.message}</span>
    </div>
  );
}

function LogSeparator() {
  return <span className="system-log-separator" aria-hidden="true"> - </span>;
}

function SystemLogState({ title, detail, tone = "neutral" }: { title: string; detail: string; tone?: "neutral" | "error" }) {
  return <div className={`system-log-state is-${tone}`}><strong>{title}</strong><span>{detail}</span></div>;
}

export function systemLogParts(record: Record<string, unknown>, timezoneName?: string | null): SystemLogParts {
  return {
    time: formatTimestampForDisplay(record.ts ?? record.timestamp, timezoneName),
    level: valueOr(record.level, "UNKNOWN").toUpperCase(),
    logger: valueOr(record.logger, "-"),
    func: valueOr(record.func ?? record.function, "-"),
    line: valueOr(record.line, "-"),
    dataflowId: valueOr(record.dataflow_id, "-"),
    message: valueOr(record.msg ?? record.message, "-"),
  };
}

export function formatSystemLogRecord(record: Record<string, unknown>, timezoneName?: string | null) {
  const parts = systemLogParts(record, timezoneName);
  return `${parts.time} - [${parts.level}] - ${parts.logger}:${parts.func}:${parts.line} - [${parts.dataflowId}] - ${parts.message}`;
}

export function logLevelTone(level: string) {
  const normalized = level.trim().toLowerCase();
  if (normalized === "fatal" || normalized === "critical") return "critical";
  if (normalized === "error" || normalized === "exception") return "error";
  if (normalized === "warning" || normalized === "warn") return "warning";
  if (normalized === "info") return "info";
  if (normalized === "debug" || normalized === "trace") return "debug";
  return "unknown";
}

function valueOr(value: unknown, fallback: string) {
  if (value === null || value === undefined) return fallback;
  const text = String(value).trim();
  return text || fallback;
}

function sortLogRecords(records: Array<Record<string, unknown>>) {
  return records.map((record, index) => ({ record, index, timestamp: parseTimestamp(valueOr(record.ts ?? record.timestamp, "")) }))
    .sort((left, right) => {
      if (left.timestamp === null && right.timestamp === null) return left.index - right.index;
      if (left.timestamp === null) return 1;
      if (right.timestamp === null) return -1;
      return left.timestamp - right.timestamp || left.index - right.index;
    })
    .map(({ record }) => record);
}

function logRecordKey(record: Record<string, unknown>, index: number) {
  return [record.ts, record.level, record.logger, record.func, record.line, record.dataflow_id, index].map(String).join(":");
}
