import { File, UploadType } from 'expo-file-system';
import type {
  CreateEncounterRequest,
  Encounter,
  EncounterMedia,
  MediaContentType,
} from '../../../../packages/contracts/src';
import { ApiError, parseApiResponse } from './errors';

/**
 * A photo held in app-controlled local storage. The uri is only ever read on
 * the device to stream the bytes; it is never sent to the API.
 */
export type LocalPhoto = { uri: string; contentType: MediaContentType };

/** The Requirement 003 encounter/media calls the capture flow needs. */
export interface EncounterClient {
  createDraftEncounter(capturedAt: string): Promise<Encounter>;
  uploadPhoto(encounterId: string, photo: LocalPhoto): Promise<EncounterMedia>;
  submitEncounter(encounterId: string): Promise<Encounter>;
  getEncounter(encounterId: string): Promise<Encounter>;
}

export function createEncounterClient(baseURL: string, getToken: () => Promise<string | null>): EncounterClient {
  if (!baseURL) throw new Error('EXPO_PUBLIC_API_URL is required');
  const base = baseURL.replace(/\/$/, '');

  async function token(): Promise<string> {
    const value = await getToken();
    if (!value) throw new ApiError('UNAUTHENTICATED', 'Sign in to save encounters');
    return value;
  }

  async function json<T>(path: string, method: 'GET' | 'POST', body?: CreateEncounterRequest): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json', Authorization: `Bearer ${await token()}` };
    if (body) headers['Content-Type'] = 'application/json';
    const response = await fetch(`${base}${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
    return parseApiResponse<T>(response.status, await response.text());
  }

  return {
    // Only capturedAt is sent: Requirement 003 never invents a location,
    // park, zone, behavior, or notes.
    createDraftEncounter: (capturedAt) => json<Encounter>('/v1/encounters', 'POST', { capturedAt }),

    // The request body is the image file itself with its exact validated
    // Content-Type: no multipart envelope, no Base64, no filename or path.
    async uploadPhoto(encounterId, photo) {
      const result = await new File(photo.uri).upload(`${base}/v1/encounters/${encodeURIComponent(encounterId)}/media`, {
        httpMethod: 'POST',
        uploadType: UploadType.BINARY_CONTENT,
        sessionType: 'foreground',
        headers: { Accept: 'application/json', Authorization: `Bearer ${await token()}`, 'Content-Type': photo.contentType },
      });
      return parseApiResponse<EncounterMedia>(result.status, result.body);
    },

    submitEncounter: (encounterId) => json<Encounter>(`/v1/encounters/${encodeURIComponent(encounterId)}/submit`, 'POST'),

    getEncounter: (encounterId) => json<Encounter>(`/v1/encounters/${encodeURIComponent(encounterId)}`, 'GET'),
  };
}
