/* eslint-disable import/first -- jest.mock factories must be declared before the modules they mock are imported. */
jest.mock('expo-file-system', () => jest.requireActual('./support/fake-file-system'));

import { fakeFs, uploadMock, UploadType } from './support/fake-file-system';
import { ApiError } from '../src/api/errors';
import { createEncounterClient } from '../src/api/encounters';

const PHOTO = { uri: 'file:///documents/toem-hia/pending-capture/capture-1.jpg', contentType: 'image/jpeg' } as const;
const fetchMock = jest.fn();

function respond(status: number, body: unknown) {
  return { status, text: () => Promise.resolve(JSON.stringify(body)) };
}

beforeEach(() => {
  fakeFs.reset();
  fetchMock.mockReset();
  global.fetch = fetchMock as unknown as typeof fetch;
});

describe('encounter/media API client', () => {
  const client = createEncounterClient('http://192.168.1.20:8080/', () => Promise.resolve('token-1'));

  it('creates a DRAFT sending only capturedAt', async () => {
    fetchMock.mockResolvedValue(respond(201, { id: 'enc-1', status: 'DRAFT' }));
    await expect(client.createDraftEncounter('2026-09-26T10:00:00.000Z')).resolves.toMatchObject({ id: 'enc-1' });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://192.168.1.20:8080/v1/encounters');
    expect(init.method).toBe('POST');
    expect(init.headers).toMatchObject({ Authorization: 'Bearer token-1', 'Content-Type': 'application/json' });
    expect(JSON.parse(init.body as string)).toEqual({ capturedAt: '2026-09-26T10:00:00.000Z' });
  });

  it('uploads the raw file with its exact Content-Type and never sends the local path', async () => {
    uploadMock.mockResolvedValue({ status: 201, body: JSON.stringify({ id: 'media-1' }), headers: {} });
    await expect(client.uploadPhoto('enc-1', PHOTO)).resolves.toEqual({ id: 'media-1' });
    const [fileUri, url, options] = uploadMock.mock.calls[0] as [string, string, Record<string, unknown>];
    expect(fileUri).toBe(PHOTO.uri);
    expect(url).toBe('http://192.168.1.20:8080/v1/encounters/enc-1/media');
    expect(options).toMatchObject({
      httpMethod: 'POST',
      uploadType: UploadType.BINARY_CONTENT,
      headers: { Authorization: 'Bearer token-1', 'Content-Type': 'image/jpeg', Accept: 'application/json' },
    });
    expect(JSON.stringify([url, options])).not.toContain('file://');
    expect(JSON.stringify(options)).not.toMatch(/base64|multipart|fieldName/i);
  });

  it('accepts 200 for a retried upload of the same bytes', async () => {
    uploadMock.mockResolvedValue({ status: 200, body: JSON.stringify({ id: 'media-1' }), headers: {} });
    await expect(client.uploadPhoto('enc-1', PHOTO)).resolves.toEqual({ id: 'media-1' });
  });

  it('maps server errors to ApiError codes', async () => {
    uploadMock.mockResolvedValue({
      status: 409,
      body: JSON.stringify({ error: { code: 'MEDIA_LIMIT_REACHED', message: 'limit', requestId: 'r1' } }),
      headers: {},
    });
    await expect(client.uploadPhoto('enc-1', PHOTO)).rejects.toMatchObject({ code: 'MEDIA_LIMIT_REACHED', requestId: 'r1' });
  });

  it('submits and reads encounters by encoded id', async () => {
    fetchMock.mockResolvedValue(respond(200, { id: 'a/b', status: 'SUBMITTED' }));
    await client.submitEncounter('a/b');
    await client.getEncounter('a/b');
    expect(fetchMock.mock.calls[0][0]).toBe('http://192.168.1.20:8080/v1/encounters/a%2Fb/submit');
    expect(fetchMock.mock.calls[1][0]).toBe('http://192.168.1.20:8080/v1/encounters/a%2Fb');
    expect((fetchMock.mock.calls[1][1] as RequestInit).method).toBe('GET');
  });

  it('fails as UNAUTHENTICATED without calling the API when there is no session', async () => {
    const guest = createEncounterClient('http://api', () => Promise.resolve(null));
    await expect(guest.createDraftEncounter('2026-09-26T10:00:00.000Z')).rejects.toEqual(
      expect.objectContaining({ code: 'UNAUTHENTICATED' }),
    );
    await expect(guest.uploadPhoto('enc-1', PHOTO)).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(uploadMock).not.toHaveBeenCalled();
  });
});
