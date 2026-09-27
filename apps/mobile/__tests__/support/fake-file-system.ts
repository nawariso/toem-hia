/**
 * In-memory stand-in for expo-file-system's File/Directory API, used by the
 * capture tests. It models only what TOEM HIA uses: exists, create, list,
 * move, delete, and upload (recorded, never sent anywhere).
 */
type Entry = File | Directory;
type Part = string | Entry;

const files = new Set<string>();
const directories = new Set<string>();

function join(parts: Part[]): string {
  return parts
    .map((part, index) => {
      const value = typeof part === 'string' ? part : part.uri;
      return index === 0 ? value.replace(/\/+$/, '') : value.replace(/^\/+|\/+$/g, '');
    })
    .join('/');
}

export const uploadMock = jest.fn();
export const moveFailure: { next: Error | null; partialTarget: boolean } = { next: null, partialTarget: false };

export class File {
  uri: string;
  constructor(...parts: Part[]) {
    this.uri = join(parts);
  }
  get exists(): boolean {
    return files.has(this.uri);
  }
  delete(): void {
    if (!files.delete(this.uri)) throw new Error('file does not exist');
  }
  async move(destination: File): Promise<void> {
    if (moveFailure.next) {
      const failure = moveFailure.next;
      moveFailure.next = null;
      if (moveFailure.partialTarget) files.add(destination.uri);
      moveFailure.partialTarget = false;
      throw failure;
    }
    if (!files.delete(this.uri)) throw new Error('source does not exist');
    files.add(destination.uri);
    this.uri = destination.uri;
  }
  upload(url: string, options: unknown): Promise<unknown> {
    return uploadMock(this.uri, url, options) as Promise<unknown>;
  }
}

export class Directory {
  uri: string;
  constructor(...parts: Part[]) {
    this.uri = join(parts);
  }
  get exists(): boolean {
    return directories.has(this.uri);
  }
  create(): void {
    directories.add(this.uri);
  }
  delete(): void {
    directories.delete(this.uri);
  }
  list(): Entry[] {
    return [...files].filter((uri) => uri.startsWith(`${this.uri}/`)).map((uri) => new File(uri));
  }
}

export const Paths = { document: new Directory('file:///documents'), cache: new Directory('file:///cache') };
export const UploadType = { BINARY_CONTENT: 0, MULTIPART: 1 } as const;

/** Test helpers. */
export const fakeFs = {
  /** Simulates the camera writing a photo into its cache. */
  cameraFile(name = `camera-${files.size}.jpg`): string {
    const uri = `file:///cache/Camera/${name}`;
    files.add(uri);
    return uri;
  },
  exists: (uri: string) => files.has(uri),
  add: (uri: string) => files.add(uri),
  all: () => [...files],
  reset() {
    files.clear();
    directories.clear();
    uploadMock.mockReset();
    moveFailure.next = null;
    moveFailure.partialTarget = false;
  },
};
