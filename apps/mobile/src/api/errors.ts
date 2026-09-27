import type { ApiError as ApiErrorBody } from '../../../../packages/contracts/src';

/** A failed API call, carrying the server's stable error code. */
export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly requestId?: string,
  ) {
    super(message);
  }
}

/**
 * Parses an API response body. 2xx with a JSON body returns it; anything else
 * becomes an ApiError with the server's code (or a generic one when the body
 * is not the error contract).
 */
export function parseApiResponse<T>(status: number, text: string): T {
  let body: unknown = null;
  try {
    body = text ? (JSON.parse(text) as unknown) : null;
  } catch {
    body = null;
  }
  if (status >= 200 && status < 300 && body !== null) return body as T;
  const error = (body as Partial<ApiErrorBody> | null)?.error;
  throw new ApiError(error?.code ?? 'REQUEST_FAILED', error?.message ?? 'Request failed', error?.requestId);
}
