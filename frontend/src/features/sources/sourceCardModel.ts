import type { SourcePath, SourceValidationResult } from "../../shared/api/domainTypes";
import type { SourceKind } from "../../shared/lib/sources";

export function sourceTypeLabel(kind: SourceKind, item: SourcePath, modeLabel: string | null) {
  if (kind === "metadata") return metadataSourceTypeLabel(item);
  if (kind === "sql") return "SQL file/root";
  if (kind === "logs") {
    return item.source_config?.mode === "separate_paths" ? "separate log base paths" : "log base path";
  }
  return modeLabel ? `${modeLabel} source` : "source code path";
}

function metadataSourceTypeLabel(item: SourcePath) {
  const marker = `${item.label ?? ""}/${item.uri}`.replace(/\\/g, "/").toLowerCase();
  if (marker.includes("schema_hints") || marker.includes("schema-hints")) return "schema hints path";
  if (marker.includes("connections")) return "connection path";
  if (marker.includes("dataflows")) return "dataflow path";
  return "metadata path";
}

export function validatedPythonFileCount(validation: SourceValidationResult | null): number | null {
  if (validation?.status !== "ok") return null;
  const count = validation.record_counts?.python_files;
  return typeof count === "number" && count > 0 ? count : null;
}

export function discoveredPythonFileCount(item: SourcePath): number | null {
  const counts = item.source_config?.discovery_record_counts;
  if (!counts || typeof counts !== "object" || Array.isArray(counts)) return null;
  const count = (counts as Record<string, unknown>).python_files;
  return typeof count === "number" && count > 0 ? count : null;
}

export function sourceBasename(uri: string) {
  const normalized = uri.replace(/\\/g, "/").replace(/\/$/, "");
  return normalized.split("/").pop() || uri;
}
