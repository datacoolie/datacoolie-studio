import { Check, Copy, LocateFixed, X } from "lucide-react";
import { useState } from "react";
import type {
  LineageAsset,
  LineageDataflow,
  LineageDependency,
  LineageReference,
  MonitoringRecord
} from "../../../shared/api/types";
import { StatusPill } from "../../../shared/components/StatusPill";
import { presentLineageAsset } from "../model/presentation";
import type { LineageEntity, LineageFocus } from "../model/types";

export function LineageDetailsDrawer({
  entity,
  dataflow,
  dependency,
  run,
  entityById,
  onClose,
  onFocusItem
}: {
  entity: LineageEntity | null;
  dataflow: LineageDataflow | null;
  dependency: LineageDependency | null;
  run: MonitoringRecord | null;
  entityById: Map<string, LineageEntity>;
  onClose: () => void;
  onFocusItem: (focus: LineageFocus) => void;
}) {
  if (!entity && !dataflow && !dependency) return null;
  return (
    <aside className="lineage-detail-drawer" aria-label="Lineage details">
      <button className="lineage-detail-close" type="button" aria-label="Close lineage details" onClick={onClose}>
        <X size={16} />
      </button>
      {entity
        ? isAsset(entity)
          ? <AssetDetails asset={entity} onFocusItem={onFocusItem} />
          : <ReferenceDetails reference={entity} onFocusItem={onFocusItem} />
        : dataflow
          ? <DataflowDetails dataflow={dataflow} run={run} entityById={entityById} onFocusItem={onFocusItem} />
          : <DependencyDetails dependency={dependency!} entityById={entityById} onFocusItem={onFocusItem} />}
    </aside>
  );
}

function AssetDetails({ asset, onFocusItem }: { asset: LineageAsset; onFocusItem: (focus: LineageFocus) => void }) {
  const presentation = presentLineageAsset(asset);
  const rows: Array<[string, string]> = [
    ["Kind", asset.kind.replace(/_/g, " ")],
    ["Format", presentation.badge],
    ["Declared", asset.declaration_status.replace(/_/g, " ")]
  ];
  if (asset.catalog) rows.push(["Catalog", asset.catalog]);
  if (asset.database) rows.push(["Database", asset.database]);
  if (asset.schema_name) rows.push(["Schema", asset.schema_name]);
  if (asset.table) rows.push(["Table", asset.table]);
  if (asset.path) rows.push(["Physical path", asset.path]);
  if (asset.python_function) rows.push(["Python function", asset.python_function]);
  return (
    <>
      <span className="eyebrow">Asset</span>
      <h3>{presentation.locator}</h3>
      <p className="lineage-detail-identity">{presentation.fullIdentity}</p>
      <DetailActions onFocus={() => onFocusItem({ kind: "asset", id: asset.id })} />
      <IdentityField label="Asset identity" value={asset.id} />
      <DetailRows rows={rows} />
      {asset.query ? <CodeBlock label="SQL query" value={asset.query} /> : null}
    </>
  );
}

function ReferenceDetails({ reference, onFocusItem }: { reference: LineageReference; onFocusItem: (focus: LineageFocus) => void }) {
  return (
    <>
      <span className="eyebrow">Unresolved dependency</span>
      <h3>{reference.display_name}</h3>
      <DetailActions onFocus={() => onFocusItem({ kind: "reference", id: reference.id })} />
      <IdentityField label="Reference identity" value={reference.id} />
      <DetailRows rows={[
        ["Status", reference.resolution_status],
        ["Kind", reference.kind.replace(/_/g, " ")],
        ["Provenance", reference.provenance],
        ["Reason", reference.reason_code],
        ["Candidates", String(reference.candidate_asset_ids.length)]
      ]} />
      <CodeBlock label="Observed expression" value={reference.raw_value} />
    </>
  );
}

function DataflowDetails({
  dataflow,
  run,
  entityById,
  onFocusItem
}: {
  dataflow: LineageDataflow;
  run: MonitoringRecord | null;
  entityById: Map<string, LineageEntity>;
  onFocusItem: (focus: LineageFocus) => void;
}) {
  const status = typeof run?.status === "string" ? run.status : "unknown";
  return (
    <>
      <span className="eyebrow">Dataflow</span>
      <div className="lineage-detail-title">
        <h3>{dataflow.name}</h3>
        <StatusPill status={status} />
      </div>
      <DetailActions onFocus={() => onFocusItem({ kind: "dataflow", id: dataflow.id })} />
      <IdentityField label="Dataflow identity" value={dataflow.id} />
      <DetailRows rows={[
        ["Runtime ID", dataflow.dataflow_id],
        ["Stage", dataflow.stage || "unknown"],
        ["Load type", dataflow.load_type || "unknown"],
        ["From", entityLabel(entityById.get(dataflow.source_asset_id))],
        ["To", entityLabel(entityById.get(dataflow.destination_asset_id))],
        ["Latest run", formatTimestamp(firstValue(run, "end_time", "completed_at", "start_time", "started_at"))],
        ["Duration", formatDuration(firstValue(run, "duration_seconds"))]
      ]} />
      {!run ? <p className="lineage-detail-note">No matching ETL run in the configured log cache.</p> : null}
    </>
  );
}

function DependencyDetails({
  dependency,
  entityById,
  onFocusItem
}: {
  dependency: LineageDependency;
  entityById: Map<string, LineageEntity>;
  onFocusItem: (focus: LineageFocus) => void;
}) {
  return (
    <>
      <span className="eyebrow">Dependency</span>
      <h3>{dependency.provenance.replace(/_/g, " ")} input</h3>
      <DetailActions onFocus={() => onFocusItem({ kind: "dependency", id: dependency.id })} />
      <IdentityField label="Dependency identity" value={dependency.id} />
      <DetailRows rows={[
        ["Status", dependency.resolution_status],
        ["Relation", dependency.kind],
        ["Method", dependency.resolution_method],
        ["From", entityLabel(entityById.get(dependency.source.id))],
        ["To", entityLabel(entityById.get(dependency.target_asset_id))]
      ]} />
      {dependency.observations.length
        ? <CodeBlock label="Evidence" value={JSON.stringify(dependency.observations, null, 2)} />
        : null}
    </>
  );
}

function DetailActions({ onFocus }: { onFocus: () => void }) {
  return (
    <div className="lineage-detail-actions">
      <button type="button" onClick={onFocus}>
        <LocateFixed size={14} />
        Focus this
      </button>
    </div>
  );
}

function IdentityField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);

  async function copyIdentity() {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  }

  return (
    <div className="lineage-canonical-identity">
      <span>{label}</span>
      <code title={value}>{value}</code>
      <button type="button" aria-label={`Copy ${label.toLowerCase()}`} onClick={copyIdentity}>
        {copied ? <Check size={13} /> : <Copy size={13} />}
      </button>
    </div>
  );
}

function CodeBlock({ label, value }: { label: string; value: string }) {
  return (
    <section className="lineage-detail-code">
      <span>{label}</span>
      <pre>{value}</pre>
    </section>
  );
}

function DetailRows({ rows }: { rows: Array<[string, string]> }) {
  return (
    <dl>
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd title={value}>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function entityLabel(entity: LineageEntity | undefined) {
  if (!entity) return "unknown";
  if (isAsset(entity)) {
    const presentation = presentLineageAsset(entity);
    return `${presentation.connection} · ${presentation.locator}`;
  }
  return entity.display_name;
}

function isAsset(entity: LineageEntity): entity is LineageAsset {
  return "declaration_status" in entity;
}

function firstValue(record: MonitoringRecord | null, ...keys: string[]) {
  if (!record) return null;
  for (const key of keys) {
    const value = record[key];
    if (value !== null && value !== undefined && value !== "") return value;
  }
  return null;
}

function formatTimestamp(value: unknown) {
  if (!value) return "not available";
  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function formatDuration(value: unknown) {
  const duration = Number(value);
  if (!Number.isFinite(duration)) return "not available";
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(duration)} s`;
}
