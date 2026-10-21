import { Check, Copy } from "lucide-react";
import { isValidElement, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { CompactBytesValue, CompactNumberValue } from "../components/monitoringPresentation";
import { formatSeconds } from "../../../shared/components/DataTable";
import { Help } from "../../../shared/components/Help";
import { copyToClipboard, formatRuntimeTimestampForDisplay, highlightJson, safeJsonStringify, GroupedDetailCard, type DetailRow } from "./detailPrimitives";
import { storyScalar, type StoryMessage } from "./dataflowStoryModel";
import type { MetricValue } from "../../../shared/lib/numberFormat";

export function StoryDisclosure({ title, children, className = "", defaultOpen = false, style, onOpen }: { title: ReactNode; children: () => ReactNode; className?: string; defaultOpen?: boolean; style?: CSSProperties; onOpen?: () => void }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <details open={open} style={style} className={`dataflow-story-disclosure ${className}`} onToggle={(event) => {
      const nextOpen = event.currentTarget.open;
      setOpen(nextOpen);
      if (nextOpen) onOpen?.();
    }}>
      <summary>{title}</summary>
      {open ? <div className="dataflow-story-disclosure-body">{children()}</div> : null}
    </details>
  );
}

export function StoryJson({ value, copyPayload }: { value: unknown; copyPayload?: string | null }) {
  const [copied, setCopied] = useState(false);
  const formatted = useMemo(() => safeJsonStringify(value), [value]);
  return (
    <div className="monitoring-json-box monitoring-inline-json-box">
      <Help as="button" type="button" className="icon-action small monitoring-json-copy" aria-label={copyPayload ? "Copy execution JSON" : "Copy JSON"}
        title="Copy JSON" onClick={() => { void copyToClipboard(copyPayload ?? formatted, setCopied); }}>
        {copied ? <Check size={12} /> : <Copy size={12} />}
      </Help>
      <pre className="monitoring-inline-json monitoring-json-light">{highlightJson(formatted)}</pre>
    </div>
  );
}

export function StoryCard({ title, rows, timezoneName, className, runtime = false }: { title: string; rows: DetailRow[]; timezoneName?: string | null; className?: string; runtime?: boolean }) {
  return <GroupedDetailCard title={title} rows={rows.map(([label, value, field]) => {
    if (runtime && typeof value === "string" && (field === "start_time" || field === "end_time")) {
      return [label, formatRuntimeTimestampForDisplay(value, timezoneName)];
    }
    if (!isValidElement(value) && value != null && field) {
      if (field.includes("bytes")) return [label, <StoryBytes value={value} />, field];
      if (/^(rows_|files_)/u.test(field)) return [label, <StoryCount value={value} />, field];
      if (field.endsWith("duration_seconds")) return [label, <StoryDuration value={value} />, field];
    }
    return [label, storyScalar(value), field];
  })} timezoneName={timezoneName} className={className} />;
}

export function StoryDuration({ value, unknownLabel = "unk" }: { value: unknown; unknownLabel?: string }) {
  const scalar = storyScalar(value);
  return scalar === null || scalar === undefined ? <span>{unknownLabel}</span>
    : <Help title={`${String(scalar)} seconds`}>{formatSeconds(Number(scalar))}</Help>;
}

export function StoryCount({ value, unknownLabel = "unk" }: { value: unknown; unknownLabel?: string }) {
  return value === null || value === undefined ? <span>{unknownLabel}</span> : <CompactNumberValue value={storyScalar(value) as MetricValue} />;
}

export function StoryBytes({ value, unknownLabel = "unk" }: { value: unknown; unknownLabel?: string }) {
  return value === null || value === undefined ? <span>{unknownLabel}</span> : <CompactBytesValue value={storyScalar(value) as MetricValue} />;
}

export function StoryMessages({ messages, attempt = false, preview = false, id }: { messages: StoryMessage[]; attempt?: boolean; preview?: boolean; id?: string }) {
  if (!messages.length) return null;
  return (
    <section id={id} className="monitoring-detail-section monitoring-dataflow-message-section">
      {attempt ? <h4>Message</h4> : <h3>Message</h3>}
      <dl className="monitoring-dataflow-messages">
        {(preview ? messages.slice(0, 1) : messages).map((message, index) => (
          <div key={index}><dt>{message.labels.join(" · ")}</dt><dd>
            <div className="monitoring-message-scroll">{message.text || <span>unk</span>}</div>
            {message.truncatedLabels.length ? <small>{message.truncatedLabels.join(" · ")}: message truncated in log.</small> : null}
            {message.coverage?.filter(issue=>issue.reason!=="truncated").map((issue,index)=><small key={index}>{issue.label}: message {issue.reason} in log.</small>)}
          </dd></div>
        ))}
      </dl>
      {preview && messages.length > 1 ? <StoryDisclosure title={`More messages (${messages.length - 1})`}>{() => <StoryMessages messages={messages.slice(1)} attempt={attempt} />}</StoryDisclosure> : null}
    </section>
  );
}
