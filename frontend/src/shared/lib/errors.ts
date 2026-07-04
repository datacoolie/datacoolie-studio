/** Normalize any thrown value into a user-facing message string. */
export function toErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}
