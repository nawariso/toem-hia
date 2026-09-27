/* eslint-disable import/first -- jest.mock factories must be declared before the modules they mock are imported. */
jest.mock('expo-file-system', () => jest.requireActual('./support/fake-file-system'));

import { fakeFs } from './support/fake-file-system';
import { createPendingCaptureStore } from '../src/capture/pending-capture';

const CAPTURED_AT = '2026-09-26T10:00:00.000Z';

beforeEach(() => fakeFs.reset());

describe('pending capture store', () => {
  it('moves the camera file into TOEM HIA document storage and stops using the camera URI', async () => {
    const store = createPendingCaptureStore();
    const cameraUri = fakeFs.cameraFile();
    const capture = await store.keep(cameraUri, 'image/jpeg', CAPTURED_AT);

    expect(capture.photo?.uri).toMatch(/^file:\/\/\/documents\/toem-hia\/pending-capture\/capture-.+\.jpg$/);
    expect(capture.photo?.contentType).toBe('image/jpeg');
    expect(fakeFs.exists(cameraUri)).toBe(false);
    expect(fakeFs.exists(capture.photo!.uri)).toBe(true);
    expect(capture).toEqual({
      photo: { uri: capture.photo!.uri, contentType: 'image/jpeg' },
      capturedAt: CAPTURED_AT,
      encounterId: null,
      uploaded: false,
      saveRequested: false,
    });
  });

  it('retake replaces the previous pending file', async () => {
    const store = createPendingCaptureStore();
    const first = await store.keep(fakeFs.cameraFile('a.jpg'), 'image/jpeg', CAPTURED_AT);
    const second = await store.keep(fakeFs.cameraFile('b.jpg'), 'image/jpeg', CAPTURED_AT);
    expect(fakeFs.exists(first.photo!.uri)).toBe(false);
    expect(fakeFs.exists(second.photo!.uri)).toBe(true);
    expect(fakeFs.all()).toEqual([second.photo!.uri]);
  });

  it('explicit discard removes the pending image', async () => {
    const store = createPendingCaptureStore();
    const capture = await store.keep(fakeFs.cameraFile(), 'image/jpeg', CAPTURED_AT);
    store.discard();
    expect(store.get()).toBeNull();
    expect(fakeFs.exists(capture.photo!.uri)).toBe(false);
  });

  it('markUploaded deletes the local copy but keeps the encounter for submit retries', async () => {
    const store = createPendingCaptureStore();
    const capture = await store.keep(fakeFs.cameraFile(), 'image/jpeg', CAPTURED_AT);
    store.setEncounter('enc-1');
    store.markUploaded();
    expect(fakeFs.exists(capture.photo!.uri)).toBe(false);
    expect(store.get()).toMatchObject({ photo: null, uploaded: true, encounterId: 'enc-1' });
  });

  it('removes files a terminated session left behind', async () => {
    fakeFs.add('file:///documents/toem-hia/pending-capture/capture-old.jpg');
    const store = createPendingCaptureStore();
    const capture = await store.keep(fakeFs.cameraFile(), 'image/jpeg', CAPTURED_AT);
    expect(fakeFs.all()).toEqual([capture.photo!.uri]);
  });

  it.each([false, true])('a failed move (partial target: %s) leaves the camera source and no pending file', async (partialTarget) => {
    const { moveFailure } = jest.requireMock('expo-file-system') as {
      moveFailure: { next: Error | null; partialTarget: boolean };
    };
    moveFailure.next = new Error('disk full');
    moveFailure.partialTarget = partialTarget;
    const store = createPendingCaptureStore();
    const cameraUri = fakeFs.cameraFile();
    await expect(store.keep(cameraUri, 'image/jpeg', CAPTURED_AT)).rejects.toThrow('disk full');
    expect(store.get()).toBeNull();
    expect(fakeFs.exists(cameraUri)).toBe(true);
    expect(fakeFs.all()).toEqual([cameraUri]);
  });

  it('notifies subscribers of every change', async () => {
    const store = createPendingCaptureStore();
    const listener = jest.fn();
    const unsubscribe = store.subscribe(listener);
    await store.keep(fakeFs.cameraFile(), 'image/jpeg', CAPTURED_AT);
    store.requestSave();
    store.discard();
    unsubscribe();
    store.discard();
    expect(listener.mock.calls.length).toBeGreaterThanOrEqual(3);
    const calls = listener.mock.calls.length;
    store.requestSave();
    expect(listener).toHaveBeenCalledTimes(calls);
  });
});
