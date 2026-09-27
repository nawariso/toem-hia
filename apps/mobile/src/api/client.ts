import type { ApiClient, ProfileInput } from '../auth/controller';
import type { User } from '../auth/state';
import { ApiError } from './errors';

type ErrorPayload = { error?: { code?: string; message?: string; requestId?: string } };
export { ApiError };
export function createApiClient(baseURL: string): ApiClient {
  if (!baseURL) throw new Error('EXPO_PUBLIC_API_URL is required');
  async function request(path: string, token: string, init: RequestInit = {}): Promise<User> {
    const response = await fetch(`${baseURL.replace(/\/$/, '')}${path}`, { ...init, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, ...init.headers } });
    const body = await response.json() as User | ErrorPayload;
    if (!response.ok) { const error = (body as ErrorPayload).error; throw new ApiError(error?.code ?? 'REQUEST_FAILED', error?.message ?? 'Request failed', error?.requestId); }
    return body as User;
  }
  return {
    bootstrap: token => request('/v1/auth/bootstrap', token, { method: 'POST' }),
    getMe: token => request('/v1/users/me', token),
    updateMe: (token: string, patch: ProfileInput) => request('/v1/users/me', token, { method: 'PATCH', body: JSON.stringify(patch) }),
  };
}
