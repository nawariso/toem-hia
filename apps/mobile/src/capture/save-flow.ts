import type { Encounter } from '../../../../packages/contracts/src';
import { ApiError } from '../api/errors';
import type { EncounterClient } from '../api/encounters';
import { pendingCapture, type PendingCaptureStore } from './pending-capture';

export type SaveStage = 'create' | 'upload' | 'submit';

/** Which step of the save failed; the pending capture keeps what already succeeded. */
export class CaptureSaveError extends Error {
  constructor(
    public readonly stage: SaveStage,
    public readonly cause: unknown,
  ) {
    super(cause instanceof Error ? cause.message : 'Save failed');
  }
}

/**
 * Saves the pending capture:
 *
 *   create DRAFT encounter -> upload photo -> submit encounter -> forget capture
 *
 * Every completed step is recorded on the pending capture, so a retry resumes
 * where the previous attempt stopped: it never creates a second encounter,
 * and never uploads again after a successful upload. Concurrent calls (double
 * taps) share one in-flight save.
 */
export function createCaptureSaver(store: PendingCaptureStore) {
  let inFlight: Promise<Encounter> | null = null;

  async function run(client: EncounterClient): Promise<Encounter> {
    const capture = store.get();
    if (!capture) throw new CaptureSaveError('create', new Error('There is no photo to save'));

    let encounterId = capture.encounterId;
    if (!encounterId) {
      try {
        encounterId = (await client.createDraftEncounter(capture.capturedAt)).id;
      } catch (cause) {
        throw new CaptureSaveError('create', cause);
      }
      store.setEncounter(encounterId);
    }

    if (!capture.uploaded) {
      const photo = store.get()?.photo;
      if (!photo) throw new CaptureSaveError('upload', new Error('The photo is no longer available'));
      try {
        await client.uploadPhoto(encounterId, photo);
      } catch (cause) {
        // The draft is gone or belongs to someone else (for example after
        // signing in as a different user): the next retry starts a new one.
        if (cause instanceof ApiError && cause.code === 'ENCOUNTER_NOT_FOUND') store.setEncounter(null);
        throw new CaptureSaveError('upload', cause);
      }
      store.markUploaded();
    }

    let submitted: Encounter;
    try {
      submitted = await client.submitEncounter(encounterId);
    } catch (cause) {
      // A previous submit may have succeeded with its response lost; the
      // server then refuses a second submit. Confirm the state instead of
      // reporting a failure for an encounter that is already submitted.
      const confirmed =
        cause instanceof ApiError && cause.code === 'ENCOUNTER_NOT_EDITABLE'
          ? await client.getEncounter(encounterId).catch(() => null)
          : null;
      if (confirmed?.status !== 'SUBMITTED') throw new CaptureSaveError('submit', cause);
      submitted = confirmed;
    }
    if (submitted.status !== 'SUBMITTED') {
      throw new CaptureSaveError('submit', new Error('The server has not confirmed submission'));
    }
    store.discard();
    return submitted;
  }

  return function save(client: EncounterClient): Promise<Encounter> {
    if (!inFlight) {
      inFlight = run(client).finally(() => {
        inFlight = null;
      });
    }
    return inFlight;
  };
}

export const savePendingCapture = createCaptureSaver(pendingCapture);
