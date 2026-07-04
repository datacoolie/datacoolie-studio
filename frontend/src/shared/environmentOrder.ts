export const ENVIRONMENT_PRESETS = ["dev", "test", "prod"] as const;

const PRESET_ORDER = new Map<string, number>(ENVIRONMENT_PRESETS.map((name, index) => [name, index]));

export function orderEnvironmentItems<T extends { name: string }>(items: T[]) {
  return [...items].sort((a, b) => compareEnvironmentName(a.name, b.name));
}

export function orderedEnvironmentNamesWithMissing<T extends { name: string }>(items: T[]) {
  const existing = orderEnvironmentItems(items).map((item) => item.name);
  const existingSet = new Set(existing);
  const missing = ENVIRONMENT_PRESETS.filter((name) => !existingSet.has(name));
  return [...existing, ...missing];
}

function compareEnvironmentName(left: string, right: string) {
  const leftPreset = PRESET_ORDER.get(left);
  const rightPreset = PRESET_ORDER.get(right);
  if (leftPreset !== undefined && rightPreset !== undefined) return leftPreset - rightPreset;
  if (leftPreset !== undefined) return -1;
  if (rightPreset !== undefined) return 1;
  return left.localeCompare(right);
}
