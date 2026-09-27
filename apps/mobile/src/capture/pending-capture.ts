import { Directory, File, Paths } from 'expo-file-system';
import type { MediaContentType } from '../../../../packages/contracts/src';
import type { LocalPhoto } from '../api/encounters';

/**
 * The one capture the user is saving. Only what is needed to resume the save
 * flow is kept:
 *
 *  - photo: the image in app-controlled document storage (null once it has
 *    been uploaded, because the local copy is deleted at that point);
 *  - capturedAt: the capture timestamp, sent as the encounter's capturedAt;
 *  - encounterId: the DRAFT encounter already created for this capture, so a
 *    retry never creates a second one;
 *  - uploaded: the photo is attached, so a retry only resubmits;
 *  - saveRequested: the user pressed Save (used to resume after sign-in).
 *
 * Metadata lives in memory: it survives screen and sign-in navigation but not
 * process termination (not required in Requirement 003). The next capture
 * removes any file left behind by a terminated process.
 */
export type PendingCapture = {
  photo: LocalPhoto | null;
  capturedAt: string;
  encounterId: string | null;
  uploaded: boolean;
  saveRequested: boolean;
};

export interface PendingCaptureStore {
  get(): PendingCapture | null;
  subscribe(listener: () => void): () => void;
  /** Moves a fresh camera file into pending storage, replacing any previous capture. */
  keep(sourceUri: string, contentType: MediaContentType, capturedAt: string): Promise<PendingCapture>;
  requestSave(): void;
  setEncounter(encounterId: string | null): void;
  /** The photo is attached server-side: delete the local copy, keep the encounter for submit retries. */
  markUploaded(): void;
  /** Retake, cancel, or completed save: delete the local copy and forget the capture. */
  discard(): void;
}

/** TOEM HIA-owned directory under the app's document storage. */
export function pendingCaptureDirectory(): Directory {
  return new Directory(Paths.document, 'toem-hia', 'pending-capture');
}

function deleteQuietly(file: File | Directory): void {
  try {
    if (file.exists) file.delete();
  } catch {
    // Best effort: a leftover file is removed by the next capture.
  }
}

export function createPendingCaptureStore(directory: () => Directory = pendingCaptureDirectory): PendingCaptureStore {
  let current: PendingCapture | null = null;
  let sequence = 0;
  const listeners = new Set<() => void>();

  function set(next: PendingCapture | null) {
    current = next;
    for (const listener of listeners) listener();
  }

  function deletePhoto() {
    if (current?.photo) deleteQuietly(new File(current.photo.uri));
  }

  return {
    get: () => current,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },

    async keep(sourceUri, contentType, capturedAt) {
      deletePhoto();
      set(null);
      const dir = directory();
      dir.create({ intermediates: true, idempotent: true });
      // Only one capture is pending at a time: clear anything a previous
      // (terminated) session left behind.
      for (const entry of dir.list()) deleteQuietly(entry);
      // A unique name per capture, so a retake never reuses the previous URI
      // (which the preview Image may still have cached).
      sequence += 1;
      const name = `capture-${Date.now().toString(36)}-${sequence}.${contentType === 'image/png' ? 'png' : 'jpg'}`;
      const target = new File(dir, name);
      const source = new File(sourceUri);
      try {
        await source.move(target);
      } catch (cause) {
        // The camera owns the source; never delete the only photo on a failed move.
        deleteQuietly(target);
        throw cause;
      }
      // The camera cache URI is not used again; only the controlled copy is.
      const next: PendingCapture = {
        photo: { uri: target.uri, contentType },
        capturedAt,
        encounterId: null,
        uploaded: false,
        saveRequested: false,
      };
      set(next);
      return next;
    },

    requestSave() {
      if (current && !current.saveRequested) set({ ...current, saveRequested: true });
    },

    setEncounter(encounterId) {
      if (current) set({ ...current, encounterId });
    },

    markUploaded() {
      if (!current) return;
      deletePhoto();
      set({ ...current, photo: null, uploaded: true });
    },

    discard() {
      deletePhoto();
      set(null);
    },
  };
}

/** The app's single pending capture, shared by Scan and the sign-in screens. */
export const pendingCapture = createPendingCaptureStore();
