/* eslint-disable import/first -- jest.mock factories must be declared before the modules they mock are imported. */
jest.mock('expo-file-system', () => jest.requireActual('./support/fake-file-system'));

import type { Encounter, EncounterMedia } from '../../../packages/contracts/src';
import { fakeFs } from './support/fake-file-system';
import { ApiError } from '../src/api/errors';
import type { EncounterClient } from '../src/api/encounters';
import { createPendingCaptureStore, type PendingCaptureStore } from '../src/capture/pending-capture';
import { CaptureSaveError, createCaptureSaver } from '../src/capture/save-flow';

const CAPTURED_AT = '2026-09-26T10:00:00.000Z';

function encounter(status: Encounter['status']): Encounter {
  return {
    id: 'enc-1',
    status,
    capturedAt: CAPTURED_AT,
    submittedAt: status === 'DRAFT' ? null : CAPTURED_AT,
    park: null,
    zone: null,
    behavior: null,
    notes: null,
    createdAt: CAPTURED_AT,
    updatedAt: CAPTURED_AT,
  };
}

const media: EncounterMedia = {
  id: 'media-1',
  encounterId: 'enc-1',
  kind: 'PHOTO',
  status: 'READY',
  visibility: 'PRIVATE',
  contentType: 'image/jpeg',
  byteSize: 1024,
  width: 640,
  height: 480,
  sha256: 'a'.repeat(64),
  createdAt: CAPTURED_AT,
};

type ClientMock = { [K in keyof EncounterClient]: jest.Mock };

function fakeClient(): ClientMock {
  return {
    createDraftEncounter: jest.fn().mockResolvedValue(encounter('DRAFT')),
    uploadPhoto: jest.fn().mockResolvedValue(media),
    submitEncounter: jest.fn().mockResolvedValue(encounter('SUBMITTED')),
    getEncounter: jest.fn().mockResolvedValue(encounter('DRAFT')),
  };
}

let store: PendingCaptureStore;
let save: ReturnType<typeof createCaptureSaver>;
let client: ClientMock;
let photoUri: string;

beforeEach(async () => {
  fakeFs.reset();
  store = createPendingCaptureStore();
  save = createCaptureSaver(store);
  client = fakeClient();
  photoUri = (await store.keep(fakeFs.cameraFile(), 'image/jpeg', CAPTURED_AT)).photo!.uri;
});

async function failingSave(): Promise<CaptureSaveError> {
  try {
    await save(client);
  } catch (error) {
    if (error instanceof CaptureSaveError) return error;
    throw error;
  }
  throw new Error('save unexpectedly succeeded');
}

describe('capture save flow', () => {
  it('creates a DRAFT with only capturedAt, uploads, submits, then removes the pending file', async () => {
    await expect(save(client)).resolves.toMatchObject({ status: 'SUBMITTED' });
    expect(client.createDraftEncounter).toHaveBeenCalledTimes(1);
    expect(client.createDraftEncounter).toHaveBeenCalledWith(CAPTURED_AT);
    expect(client.uploadPhoto).toHaveBeenCalledWith('enc-1', { uri: photoUri, contentType: 'image/jpeg' });
    expect(client.submitEncounter).toHaveBeenCalledWith('enc-1');
    expect(client.uploadPhoto.mock.invocationCallOrder[0]).toBeGreaterThan(client.createDraftEncounter.mock.invocationCallOrder[0]);
    expect(client.submitEncounter.mock.invocationCallOrder[0]).toBeGreaterThan(client.uploadPhoto.mock.invocationCallOrder[0]);
    expect(store.get()).toBeNull();
    expect(fakeFs.exists(photoUri)).toBe(false);
  });

  it('upload failure keeps the pending image and the DRAFT; Retry reuses the same encounter', async () => {
    client.uploadPhoto.mockRejectedValueOnce(new ApiError('NETWORK', 'offline'));
    const failure = await failingSave();
    expect(failure.stage).toBe('upload');
    expect(fakeFs.exists(photoUri)).toBe(true);
    expect(store.get()).toMatchObject({ encounterId: 'enc-1', uploaded: false });
    expect(client.submitEncounter).not.toHaveBeenCalled();

    await save(client);
    expect(client.createDraftEncounter).toHaveBeenCalledTimes(1);
    expect(client.uploadPhoto).toHaveBeenCalledTimes(2);
    expect(store.get()).toBeNull();
  });

  it('create failure keeps the pending image and creates the encounter on Retry', async () => {
    client.createDraftEncounter.mockRejectedValueOnce(new Error('offline'));
    expect((await failingSave()).stage).toBe('create');
    expect(fakeFs.exists(photoUri)).toBe(true);
    await save(client);
    expect(client.createDraftEncounter).toHaveBeenCalledTimes(2);
    expect(client.uploadPhoto).toHaveBeenCalledTimes(1);
  });

  it('submit failure never re-uploads the photo on Retry', async () => {
    client.submitEncounter.mockRejectedValueOnce(new ApiError('NETWORK', 'offline'));
    expect((await failingSave()).stage).toBe('submit');
    expect(store.get()).toMatchObject({ encounterId: 'enc-1', uploaded: true, photo: null });

    await save(client);
    expect(client.createDraftEncounter).toHaveBeenCalledTimes(1);
    expect(client.uploadPhoto).toHaveBeenCalledTimes(1);
    expect(client.submitEncounter).toHaveBeenCalledTimes(2);
    expect(store.get()).toBeNull();
  });

  it('a submit whose response was lost is confirmed instead of reported as failed', async () => {
    client.submitEncounter.mockRejectedValueOnce(new ApiError('ENCOUNTER_NOT_EDITABLE', 'already submitted'));
    client.getEncounter.mockResolvedValueOnce(encounter('SUBMITTED'));
    await expect(save(client)).resolves.toMatchObject({ status: 'SUBMITTED' });
    expect(store.get()).toBeNull();
  });

  it.each(['DRAFT', 'PROCESSING', 'NEEDS_REVIEW', 'CONFIRMED', 'REJECTED'])(
    'does not claim success when confirmation reports %s',
    async (status) => {
      client.submitEncounter.mockRejectedValueOnce(new ApiError('ENCOUNTER_NOT_EDITABLE', 'conflict'));
      client.getEncounter.mockResolvedValueOnce({ ...encounter('DRAFT'), status });
      expect((await failingSave()).stage).toBe('submit');
      expect(store.get()).toMatchObject({ encounterId: 'enc-1', uploaded: true, photo: null });
      expect(client.createDraftEncounter).toHaveBeenCalledTimes(1);
      expect(client.uploadPhoto).toHaveBeenCalledTimes(1);
      client.submitEncounter.mockResolvedValueOnce(encounter('SUBMITTED'));
      await expect(save(client)).resolves.toMatchObject({ status: 'SUBMITTED' });
      expect(client.createDraftEncounter).toHaveBeenCalledTimes(1);
      expect(client.uploadPhoto).toHaveBeenCalledTimes(1);
      expect(store.get()).toBeNull();
    },
  );

  it.each(['DRAFT', 'PROCESSING', 'NEEDS_REVIEW', 'CONFIRMED', 'REJECTED'])(
    'does not claim success when submit responds with %s',
    async (status) => {
      client.submitEncounter.mockResolvedValueOnce({ ...encounter('DRAFT'), status });
      expect((await failingSave()).stage).toBe('submit');
      expect(store.get()).toMatchObject({ encounterId: 'enc-1', uploaded: true, photo: null });
      client.submitEncounter.mockResolvedValueOnce(encounter('SUBMITTED'));
      await expect(save(client)).resolves.toMatchObject({ status: 'SUBMITTED' });
      expect(client.createDraftEncounter).toHaveBeenCalledTimes(1);
      expect(client.uploadPhoto).toHaveBeenCalledTimes(1);
      expect(store.get()).toBeNull();
    },
  );

  it('concurrent saves (double tap) share one in-flight save and one encounter', async () => {
    const results = await Promise.all([save(client), save(client), save(client)]);
    expect(new Set(results).size).toBe(1);
    expect(client.createDraftEncounter).toHaveBeenCalledTimes(1);
    expect(client.uploadPhoto).toHaveBeenCalledTimes(1);
    expect(client.submitEncounter).toHaveBeenCalledTimes(1);
  });

  it('starts a new DRAFT when the old one is no longer the caller’s', async () => {
    client.uploadPhoto.mockRejectedValueOnce(new ApiError('ENCOUNTER_NOT_FOUND', 'not found'));
    await failingSave();
    expect(store.get()?.encounterId).toBeNull();
    await save(client);
    expect(client.createDraftEncounter).toHaveBeenCalledTimes(2);
  });

  it('refuses to save when there is no pending capture', async () => {
    store.discard();
    expect((await failingSave()).stage).toBe('create');
    expect(client.createDraftEncounter).not.toHaveBeenCalled();
  });
});
