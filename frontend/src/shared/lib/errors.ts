/** Normalize any thrown value into a user-facing message string. */
export function toErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

export interface ApiErrorDetail {
  code?: string;
  message?: string;
  action?: string;
  reason?: string;
  source_ids?: number[];
  missing_source_ids?: number[];
  [key: string]: unknown;
}

export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: ApiErrorDetail | string | null,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }

  get code() {
    return typeof this.detail === "object" && this.detail ? this.detail.code : undefined;
  }
}

export function apiRequestError(body: string, status: number): ApiRequestError {
  let detail: ApiErrorDetail | string | null = null;
  if (body) {
    try {
      const parsed = JSON.parse(body) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
      else if (parsed.detail && typeof parsed.detail === "object") detail = parsed.detail as ApiErrorDetail;
    } catch {
      detail = body;
    }
  }
  const message = typeof detail === "string"
    ? detail
    : detail?.message ?? (body || `Request failed: ${status}`);
  return new ApiRequestError(message, status, detail);
}

export function apiErrorMessage(body: string, status: number): string {
  return apiRequestError(body, status).message;
}
