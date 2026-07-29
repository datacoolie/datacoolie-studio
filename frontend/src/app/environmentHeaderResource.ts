export const DEFAULT_SOURCE_CHECK_INTERVAL_SECONDS = 30;

export function sourceCheckIntervalMs(
  value: number | null | undefined,
  mode: "fixed" | "adaptive" | null | undefined = "fixed",
  maxValue?: number | null,
) {
  const selected = mode === "adaptive" ? (maxValue ?? value) : value;
  const seconds = Number.isInteger(selected) && Number(selected) >= 5 && Number(selected) <= 3600
    ? Number(selected)
    : DEFAULT_SOURCE_CHECK_INTERVAL_SECONDS;
  return seconds * 1_000;
}
