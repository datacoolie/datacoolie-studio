import type { AssetAttention, AssetInventoryItem, AssetReferenceGroupItem, LineageAsset } from "../../shared/api/domainTypes";
import { presentReferenceResolution, type ReferenceResolutionPresentation as SharedReferenceResolutionPresentation } from "../../shared/referenceResolutionPresentation";
import type { AssetIconKind } from "../lineage/model/presentation";
import { assetIconKind, lineageNodeSearchValues, presentLineageAsset, referenceTypeAssetType } from "../lineage/model/presentation";

interface PresentedAsset {
  iconKind: AssetIconKind;
  badge: string;
  locator: string;
  fullIdentity: string;
  connection?: string;
  friendlyName: string;
  subtitle?: string;
}

export interface ReferenceResolutionPresentation extends SharedReferenceResolutionPresentation {
  detail: string;
}

export function presentAsset(asset: AssetInventoryItem): PresentedAsset {
  const lineagePresentation = presentLineageAsset(asLineageAsset(asset));
  return {
    ...lineagePresentation,
    friendlyName: asset.friendly_name || lineagePresentation.locator,
    fullIdentity: asset.full_identity || lineagePresentation.fullIdentity,
  };
}

export function presentReference(reference: AssetReferenceGroupItem): PresentedAsset {
  return {
    iconKind: assetIconKind(referenceTypeAssetType(reference.reference_type)),
    badge: "REF",
    locator: reference.display_name,
    friendlyName: reference.display_name,
    fullIdentity: reference.normalized_value,
    subtitle: referenceContextLine(reference),
  };
}

export function assetSearchValues(asset: AssetInventoryItem): string[] {
  const lineageAsset = asLineageAsset(asset);
  const qualifiedTable = [asset.catalog, asset.database, asset.schema_name, asset.table].filter(Boolean).join(".");
  const values = [
    ...lineageNodeSearchValues(lineageAsset),
    asset.friendly_name,
    asset.full_identity,
    qualifiedTable,
    asset.connection_type,
    ...asset.roles,
    ...(asset.metadata_sources ?? []).map((source) => source.uri),
  ];
  return Array.from(new Set(values.filter((value): value is string => Boolean(value))));
}

export function referenceSearchValues(reference: AssetReferenceGroupItem): string[] {
  const values = [
    reference.id,
    reference.display_name,
    reference.normalized_value,
    reference.reference_type,
    reference.resolution.state,
    reference.resolution.reason,
    ...reference.provenances,
    ...reference.consumer_assets.map((asset) => asset.friendly_name || asset.display_name),
    reference.resolved_asset?.friendly_name,
    reference.resolved_asset?.display_name,
    ...reference.candidate_assets.map((asset) => asset.friendly_name || asset.display_name),
    ...reference.attention_items.map((item) => item.message),
  ];
  return Array.from(new Set(values.filter((value): value is string => Boolean(value))));
}

export function referenceResolutionPresentation(reference: AssetReferenceGroupItem): ReferenceResolutionPresentation {
  const presentation = presentReferenceResolution(reference.resolution);
  if (reference.resolution.state === "manual" && reference.manual_mapping?.mapping_id) {
    return { ...presentation, detail: `Mapping #${reference.manual_mapping.mapping_id}` };
  }
  if (reference.resolution.state === "automatic") {
    return { ...presentation, detail: "Resolved target" };
  }
  return { ...presentation, detail: presentation.detail || "Needs mapping" };
}

export function attentionContextLine(item: AssetAttention): string {
  return [
    attentionSourceLabel(item),
    attentionConditionLabel(item),
    `fix: ${attentionFixTarget(item)}`,
  ].filter(Boolean).join(" · ");
}

export function referenceContextLine(reference: AssetReferenceGroupItem): string {
  return [
    compactHumanize(reference.reference_type),
    referenceUsefulScope(reference),
  ].filter(Boolean).join(" · ");
}

export function referenceProvenanceLabel(value: string | null | undefined): string {
  return compactHumanize(value);
}

export function referenceProvenanceTone(value: string | null | undefined): "sql" | "python" | "mixed" | "default" {
  const normalized = String(value || "").trim().toLowerCase().replace(/[\s-]+/gu, "_");
  if (normalized === "sql") return "sql";
  if (normalized === "python") return "python";
  if (normalized === "python_sql") return "mixed";
  return "default";
}

export function referenceProvenanceDescription(value: string | null | undefined): string {
  const tone = referenceProvenanceTone(value);
  if (tone === "sql") return "Detected from SQL analysis";
  if (tone === "python") return "Detected from Python analysis";
  if (tone === "mixed") return "Detected from embedded SQL in Python analysis";
  return `Detected from ${referenceProvenanceLabel(value)} analysis`;
}

export function referenceConsumerTypeSummary(reference: AssetReferenceGroupItem): string | null {
  const typeCounts = new Map<string, number>();
  for (const asset of reference.consumer_assets) {
    const label = compactHumanize(asset.asset_type);
    typeCounts.set(label, (typeCounts.get(label) || 0) + 1);
  }
  if (!typeCounts.size) return null;
  return [...typeCounts.entries()]
    .sort(([leftLabel, leftCount], [rightLabel, rightCount]) => rightCount - leftCount || leftLabel.localeCompare(rightLabel))
    .map(([label, count]) => `${count} ${label}`)
    .join(", ");
}

export function metadataQueryForAsset(asset: AssetInventoryItem): string {
  return asset.table
    || pathBasename(asset.path)
    || asset.connection_name
    || asset.friendly_name
    || asset.display_name
    || asset.id;
}

function attentionSourceLabel(item: AssetAttention) {
  if (item.source_type === "sql_reference") return "sql_reference";
  if (item.source_type === "python_reference") return "py_reference";
  if (item.source_type === "python_sql_reference") return "py_sql_reference";
  if (item.source_type === "lineage_diagnostic") return attentionDiagnosticOrigin(item);
  return compactHumanize(item.source_type);
}

function attentionDiagnosticOrigin(item: AssetAttention) {
  if (item.code.startsWith("dynamic_") || item.code.startsWith("python_")) return "py analysis";
  if (item.code.startsWith("sql_")) return "sql analysis";
  if (item.code.includes("dataflow")) return "dataflow metadata";
  if (item.code.includes("identity") || item.metadata_source_id) return "metadata identity";
  return item.subject_type && item.subject_type !== "asset" ? `${humanize(item.subject_type)} diagnostic` : "diagnostic";
}

function attentionConditionLabel(item: AssetAttention) {
  const code = item.code.replace(/^reference_/, "").replace(/^dependency_/, "");
  if (code === "target_missing") return "mapping target missing";
  return humanize(code);
}

function attentionFixTarget(item: AssetAttention) {
  if (item.reference_id) return "reference mapping";
  if (item.dataflow_id) return `dataflow ${compactIdentityTail(item.dataflow_id)}`;
  if (item.metadata_source_id) return `metadata source #${item.metadata_source_id}`;
  if (item.source_type === "lineage_diagnostic") return "asset details";
  return "review details";
}

function humanize(value: string | null | undefined) {
  if (!value) return "-";
  return value.replace(/_/g, " ");
}

function compactHumanize(value: string | null | undefined) {
  return humanize(value)
    .replace(/\bpython sql\b/giu, "py_sql")
    .replace(/\bpython function\b/giu, "py_function")
    .replace(/\bsql query\b/giu, "sql_query")
    .replace(/\bpython\b/giu, "py")
    .replace(/\s+/gu, "_");
}

function referenceUsefulScope(reference: AssetReferenceGroupItem) {
  const normalized = compactReferenceValue(reference.normalized_value, reference.reference_type);
  if (normalized && !sameReferenceText(reference.display_name, normalized) && !sameReferenceText(reference.display_name, reference.normalized_value)) {
    return normalized;
  }
  return null;
}

function compactReferenceValue(value: string | null | undefined, referenceType: string) {
  if (!value) return null;
  if (referenceType === "api_endpoint_reference") {
    const match = value.match(/\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S+)/iu);
    if (!match) return value;
    return `${match[1].toUpperCase()} ${compactUrlPath(match[2])}`;
  }
  return value;
}

function compactUrlPath(value: string) {
  try {
    const url = new URL(value);
    return `${url.pathname || "/"}${url.search || ""}`;
  } catch {
    return value;
  }
}

function sameReferenceText(left: string | null | undefined, right: string | null | undefined) {
  if (!left || !right) return false;
  return normalizeReferenceText(left) === normalizeReferenceText(right);
}

function normalizeReferenceText(value: string) {
  return value.toLocaleLowerCase().replace(/\\/g, "/").replace(/\/+$/u, "").trim();
}

function compactIdentityTail(value: string) {
  if (value.length <= 14) return value;
  return value.slice(0, 12);
}

function asLineageAsset(asset: AssetInventoryItem): LineageAsset {
  return {
    id: asset.id,
    entity_type: "asset",
    label: asset.display_name,
    asset_type: asset.asset_type,
    display_name: asset.display_name,
    declaration_status: "declared",
    connection_name: asset.connection_name ?? undefined,
    connection_type: asset.connection_type ?? undefined,
    format: asset.format ?? undefined,
    catalog: asset.catalog ?? undefined,
    database: asset.database ?? undefined,
    schema_name: asset.schema_name ?? undefined,
    path: asset.path ?? undefined,
    table: asset.table ?? undefined,
    query: asset.query ?? undefined,
    python_function: asset.python_function ?? undefined,
    metadata_source_ids: asset.metadata_source_ids,
    roles: asset.roles,
    observations: asset.observations ?? [],
    identifiers: asset.identifiers ?? [],
  };
}

function pathBasename(path: string | null | undefined) {
  if (!path) return null;
  const normalized = path.replace(/\\/g, "/").replace(/\/+$/u, "");
  if (!normalized) return null;
  const parts = normalized.split("/").filter(Boolean);
  return parts.at(-1) || normalized;
}
