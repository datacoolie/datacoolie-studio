type MessageEntry = { label: string; value: unknown; truncated?: unknown };

export function DataflowMessageSection({ row }: { row: Record<string, unknown> }) {
  const entries = [
    { label: "Run", value: row.message, truncated: row.message_truncated },
    ...["source", "transform", "destination"].map((phase) => ({
      label: phase[0].toUpperCase() + phase.slice(1),
      value: row[`${phase}_message`],
      truncated: row[`${phase}_message_truncated`],
    })),
  ];
  if (!entries.some(hasMessage)) return null;
  return (
    <section className="monitoring-detail-section monitoring-dataflow-message-section">
      <h3>Message</h3>
      <MessageList entries={entries} />
    </section>
  );
}

export function DataflowAttemptMessages({ attempt }: { attempt: Record<string, unknown> }) {
  const entries: MessageEntry[] = [{ label: "Attempt", value: attempt.message, truncated: attempt.message_truncated }];
  for (const phase of ["source", "transform", "destination"]) {
    const value = attempt[phase];
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const record = value as Record<string, unknown>;
      entries.push({ label: phase[0].toUpperCase() + phase.slice(1), value: record.message, truncated: record.message_truncated });
    }
  }
  if (!entries.some(hasMessage)) return null;
  return (
    <section className="monitoring-detail-subsection monitoring-dataflow-message-section">
      <h6>Message</h6>
      <MessageList entries={entries} />
    </section>
  );
}

function hasMessage(entry: MessageEntry) {
  return (typeof entry.value === "string" && entry.value.trim() !== "") || entry.truncated === true;
}

function MessageList({ entries }: { entries: MessageEntry[] }) {
  return (
    <dl className="monitoring-dataflow-messages">
      {entries.filter(hasMessage).map(({ label, value, truncated }) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>
            <div className="monitoring-message-scroll">{typeof value === "string" ? value : null}</div>
            {truncated === true ? <small>Message truncated in log.</small> : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}
