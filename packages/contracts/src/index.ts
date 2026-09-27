export type CurrentUser = {
  id: string;
  username: string | null;
  displayName: string | null;
  avatarUrl: string | null;
  locale: string;
  roles: string[];
  profileComplete: boolean;
};
export type ApiError = { error: { code: string; message: string; requestId: string } };

// Requirement 002 — wildlife domain foundation.

export type ListResponse<T> = { items: T[] };

export type Park = {
  id: string;
  slug: string;
  name: string;
  city: string;
  countryCode: string;
  timezone: string;
};

export type Zone = { id: string; parkId: string; slug: string; name: string };

export type HiaStatus = 'PROVISIONAL' | 'CONFIRMED' | 'INACTIVE' | 'ARCHIVED' | 'MERGED';

/** Public hia. The internal UUID is never exposed; publicCode (HIA-000001) is the public identity. */
export type Hia = {
  publicCode: string;
  nickname: string | null;
  status: HiaStatus;
  homeParkId: string | null;
  firstSeenAt: string | null;
  confirmedAt: string | null;
  mergedIntoPublicCode: string | null;
};

export type EncounterStatus = 'DRAFT' | 'SUBMITTED' | 'PROCESSING' | 'NEEDS_REVIEW' | 'CONFIRMED' | 'REJECTED';
export type EncounterBehavior = 'BASKING' | 'SWIMMING' | 'WALKING' | 'RESTING' | 'EATING' | 'CLIMBING' | 'OTHER';
export type LocationSource = 'GPS' | 'MANUAL' | 'IMPORT' | 'UNKNOWN';

export type PlaceRef = { id: string; name: string };

/**
 * Encounter response. It has no location, observer, or hia field: precise
 * location is write-only private data, and hia linkage belongs to a future
 * Identification/Verification requirement.
 */
export type Encounter = {
  id: string;
  status: EncounterStatus;
  capturedAt: string;
  submittedAt: string | null;
  park: PlaceRef | null;
  zone: PlaceRef | null;
  behavior: EncounterBehavior | null;
  notes: string | null;
  createdAt: string;
  updatedAt: string;
};

/** Write-only precise location (WGS 84). */
export type LocationInput = {
  latitude: number;
  longitude: number;
  accuracyMeters?: number | null;
  source: LocationSource;
};

export type CreateEncounterRequest = {
  capturedAt: string;
  parkId?: string | null;
  zoneId?: string | null;
  behavior?: EncounterBehavior | null;
  notes?: string | null;
  location?: LocationInput | null;
};

/** Absent = unchanged; null clears the field. capturedAt cannot be null. */
export type UpdateEncounterRequest = Partial<Omit<CreateEncounterRequest, 'capturedAt'>> & { capturedAt?: string };

// Requirement 003 — camera & local media foundation.

export type MediaContentType = 'image/jpeg' | 'image/png';
export type MediaStatus = 'READY' | 'REJECTED';

/**
 * Private photo attached to one of the caller's encounters. There is no
 * storage key, file path, device URI, EXIF, or location field: storage is an
 * internal server detail, and precise location stays in the separate
 * write-only encounter location.
 *
 * Upload: POST /v1/encounters/{id}/media with the raw image as the request
 * body and Content-Type image/jpeg or image/png (201 created, 200 for a
 * retried upload of the same bytes). Bytes: GET /v1/media/{id}/content.
 */
export type EncounterMedia = {
  id: string;
  encounterId: string;
  kind: 'PHOTO';
  status: MediaStatus;
  visibility: 'PRIVATE';
  contentType: MediaContentType;
  byteSize: number;
  width: number;
  height: number;
  sha256: string;
  createdAt: string;
};

export type MediaErrorCode =
  | 'MEDIA_TOO_LARGE'
  | 'UNSUPPORTED_MEDIA_TYPE'
  | 'INVALID_IMAGE'
  | 'IMAGE_DIMENSIONS_EXCEEDED'
  | 'MEDIA_LIMIT_REACHED'
  | 'MEDIA_NOT_FOUND'
  | 'MEDIA_UNAVAILABLE'
  | 'ENCOUNTER_NOT_FOUND'
  | 'ENCOUNTER_NOT_EDITABLE'
  | 'USER_NOT_ACTIVE'
  | 'USER_NOT_BOOTSTRAPPED';
