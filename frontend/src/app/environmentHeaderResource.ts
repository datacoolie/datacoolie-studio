export const DEFAULT_SOURCE_CHECK_INTERVAL_SECONDS = 30;

export function sourceCheckIntervalMs(value: number | null | undefined) {
  const seconds = Number.isInteger(value) && Number(value) >= 5 && Number(value) <= 3600
    ? Number(value)
    : DEFAULT_SOURCE_CHECK_INTERVAL_SECONDS;
  return seconds * 1_000;
}
