export type ManualSourceKind = "metadata" | "sql" | "code";

export type ManualSourceCopy = {
  heading: string;
  description: string;
  pathLabel: string;
  pathPlaceholder: string;
  pathHint: string;
  submitLabel: string;
};

const MANUAL_SOURCE_COPY: Record<ManualSourceKind, ManualSourceCopy> = {
  metadata: {
    heading: "Import metadata",
    description: "Metadata file or folder.",
    pathLabel: "Metadata path",
    pathPlaceholder: "Metadata file or folder",
    pathHint: "A folder imports its metadata files.",
    submitLabel: "Import metadata",
  },
  sql: {
    heading: "Add SQL source",
    description: "One SQL file or folder.",
    pathLabel: "SQL file or root",
    pathPlaceholder: "SQL file or root folder",
    pathHint: "One path creates one SQL source.",
    submitLabel: "Add SQL source",
  },
  code: {
    heading: "Add source code",
    description: "One source-code artifact path.",
    pathLabel: "Code artifact",
    pathPlaceholder: "Directory, ZIP, wheel, or installed package",
    pathHint: "One path creates one code artifact.",
    submitLabel: "Add code artifact",
  },
};

export function manualSourceCopy(kind: ManualSourceKind): ManualSourceCopy {
  return MANUAL_SOURCE_COPY[kind];
}

export function sourcePathValues(paths: readonly string[]): string[] | undefined {
  const values = paths.map((path) => path.trim()).filter(Boolean);
  return values.length ? values : undefined;
}

export function replaceSourcePath(
  paths: readonly string[],
  index: number,
  value: string,
): string[] {
  return paths.map((path, pathIndex) => (pathIndex === index ? value : path));
}

export function addSourcePath(paths: readonly string[]): string[] {
  return [...paths, ""];
}

export function removeSourcePath(
  paths: readonly string[],
  index: number,
): string[] {
  const remaining = paths.filter((_, pathIndex) => pathIndex !== index);
  return remaining.length ? remaining : [""];
}

export function formatSourceImportSummary({
  summary,
  created = 0,
  existing = 0,
}: {
  summary: Record<string, number>;
  created?: number;
  existing?: number;
}) {
  const count = (key: string, fallback: number) => {
    const value = summary[key];
    return typeof value === "number" ? value : fallback;
  };

  const createdCount = count("created", created);
  const existingCount = count("existing", existing);
  const metadataCount = count("metadata_sources", 0);
  const sqlCount = count("sql_sources", 0);
  const codeCount = count("code_artifacts", 0);

  return `${createdCount} created · ${existingCount} reused · ${metadataCount} metadata · ${sqlCount} SQL source${sqlCount === 1 ? "" : "s"} · ${codeCount} code`;
}

type SetupSourceKind = Exclude<ManualSourceKind, "code"> | "code";

const COMPONENT_KEYS: Record<SetupSourceKind, string> = {
  metadata: "metadata",
  sql: "sql",
  code: "functions",
};

const PROVENANCE_LABELS: Record<string, string> = {
  "datacoolie.yml": "From datacoolie.yml",
  "manifest.json": "From manifest.json",
  default_input: "From submitted fallback",
  default: "From built-in default",
};

export function sourceComponentProvenanceLabel(
  kind: SetupSourceKind,
  sourceConfig?: Record<string, unknown> | null,
) {
  if (sourceConfig?.discovery_managed !== true) return null;
  const resolvedFrom = sourceConfig.component_resolved_from;
  if (!resolvedFrom || typeof resolvedFrom !== "object" || Array.isArray(resolvedFrom)) return null;

  const value = (resolvedFrom as Record<string, unknown>)[COMPONENT_KEYS[kind]];
  return typeof value === "string" ? PROVENANCE_LABELS[value] ?? null : null;
}
