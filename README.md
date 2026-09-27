# TOEM HIA

Community monitor-lizard application. Requirement 001 (foundation) and 001-B (local development mode) are accepted. Requirement 002 (wildlife domain foundation: parks, zones, Hias, encounters, private encounter locations on PostgreSQL + PostGIS) is **accepted** and merged. See `docs/requirements/002-wildlife-domain-foundation.md`.

A fresh clone runs the mobile app, Go API, PostgreSQL, and authentication **without a Supabase account, email provider, OTP, or any cloud account**, using Controlled Local Development Mode (`AUTH_MODE=local`).

> **LOCAL AUTH IS DEVELOPMENT ONLY. IT MUST NEVER BE ENABLED IN PRODUCTION.**
> `APP_ENV=production` + `AUTH_MODE=local` is a fatal startup error, and release mobile builds refuse local mode.

Status: REQ-001, REQ-001-B and REQ-002 accepted; Requirement 003 is implemented pending independent review. Native local-auth smoke on a device/emulator is **NOT RUN**; the Requirement 003 camera flow is also **DEVICE VALIDATION — NOT RUN**. Both are mandatory before any public or user pilot. Supabase Auth / Email OTP / real provider JWT are **DEFERRED TO INTEGRATION & PILOT HARDENING** — not tested end to end. See `docs/requirements/001B-local-development-mode.md` and `docs/requirements/003-camera-local-media-foundation.md`.

## Architecture and cost

- Expo SDK 57 / React Native 0.86.3 / React 19.2.3 mobile app with Expo Router. Auth goes through an `AuthProvider` adapter: `LocalDevAuthProvider` (development) or `SupabaseAuthProvider` (email OTP, deferred).
- Go 1.27.1 modular-monolith API. Domain and application layers do not depend on Supabase or pgx.
- PostgreSQL 18.6 + PostGIS 3.6.4 as the system of record, run locally with Docker Compose (`postgis/postgis:18-3.6-alpine`, pinned by digest; the same image runs in CI).
- API identity goes through `IdentityVerifier`: `LocalDevVerifier` (`AUTH_MODE=local`) or the Supabase JWKS/JWT verifier (`AUTH_MODE=supabase`). Supabase Auth Free Tier remains the selected provider for Integration & Pilot Hardening.
- Mandatory infrastructure cost: **$0/month**. Local development has no external runtime dependency.

See `docs/architecture/foundation.md` and `docs/adr/`.

## Naming

| Concept | Canonical value |
| --- | --- |
| Product name | `TOEM HIA` (normal English form `Toem Hia`) |
| Thai brand | `เติมเหี้ย` |
| Tagline | `Every Hia Has a Story.` |
| Animal/community noun | `Hia` (plural `Hias`; technical noun `hia`) |
| Public animal code | `HIA-000001` |
| Product slug / repository | `toem-hia` / `github.com/nawariso/toem-hia` |
| Go module | `github.com/nawariso/toem-hia/services/api` |
| Mobile | package `@toem-hia/mobile`, Expo slug `toem-hia`, scheme `toemhia`, bundle/package `com.toemhia.mobile` |
| Local database | Compose project `toem-hia`, user/database `toem_hia`, volume `toem_hia_postgres` |

The previous name is legacy; Git history is intentionally not rewritten. If you have a local Docker volume from the previous name, it is not reused or deleted automatically — the `toem-hia` Compose project starts a fresh database (run migrations and the seed). Remove the old volume yourself once you no longer need it.

## Prerequisites

Install stable versions:

- Git
- Docker Desktop / Docker Engine with Compose v2+
- Go 1.27.1
- Node.js 24.3.0 exactly (`.nvmrc` and `.node-version`)
- npm 11.4.2 exactly (`packageManager`, `devEngines`, and `apps/mobile/.npmrc`)
- Expo Go or an Android/iOS simulator
- (Supabase mode only, deferred) a free Supabase project with asymmetric JWT signing keys

Pinned product versions are in `services/api/go.mod`, `apps/mobile/package.json`, and `apps/mobile/package-lock.json`. Use `nvm use` (or an equivalent version manager) from the repository root before running npm. npm rejects a different Node/npm toolchain so a fresh developer cannot silently regenerate a materially different lockfile. The lockfile is generated and checked in CI's exact Node 24.3.0 / npm 11.4.2 environment.

## 1. Clone and configure

```bash
git clone https://github.com/nawariso/toem-hia.git
cd toem-hia
cp .env.example .env.local
```

Edit `.env.local`:

1. Replace the local database password placeholder in both `POSTGRES_PASSWORD` and `DATABASE_URL`.
2. Keep `APP_ENV=development`, `AUTH_MODE=local`, `MEDIA_MODE=local`, `EXPO_PUBLIC_APP_ENV=development`, `EXPO_PUBLIC_AUTH_MODE=local`. An existing `.env.local` from before Requirement 003 must gain `MEDIA_MODE` and `MEDIA_LOCAL_ROOT` (see `.env.example`); the API refuses to start without `MEDIA_MODE`.
3. Set `EXPO_PUBLIC_API_URL` to this computer's LAN URL (for example `http://192.168.1.20:8080`) when testing on a physical phone. A phone cannot reach the computer through its own `localhost`.

No Supabase value is needed in local mode. `.env.local` is ignored by Git. `.env.example` contains placeholders only.

### Authentication modes

| `APP_ENV` | `AUTH_MODE` | Result |
| --- | --- | --- |
| `development` / `test` | `local` | Starts. Deterministic dev user `LOCAL_DEV/developer-001`. No Supabase config read. |
| any | `supabase` | Starts only when `AUTH_ISSUER`, `AUTH_AUDIENCE`, `AUTH_JWKS_URL` are set (mobile also needs `EXPO_PUBLIC_SUPABASE_URL`, `EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY`). |
| `production` | `local` | **Fatal startup error.** |
| missing / unknown | missing / unknown | Fatal startup error. No defaults, no fallback. |

The local credential is a fixed, non-sensitive development string. The API still verifies it through `IdentityVerifier` and resolves the internal user through the normal bootstrap; the client never sends a user ID, role, or status. A Supabase-mode API rejects it.

### Local media (Requirement 003)

| `APP_ENV` | `MEDIA_MODE` | Result |
| --- | --- | --- |
| `development` / `test` | `local` | Starts. Photos are stored under `MEDIA_LOCAL_ROOT` on this computer (directories created `0700`; files created through `os.CreateTemp`, `0600` on Unix). |
| any | `disabled` | Starts. Media routes answer `503 MEDIA_UNAVAILABLE`. |
| `production` | `local` | **Fatal startup error.** |
| missing / unknown | missing / unknown | Fatal startup error. No default. |

- Local media is **development/test only**. It is a `MediaStore` adapter (ADR-010); a production media store is a later requirement.
- No cloud media provider, bucket, or account is needed. Mandatory external runtime cost stays **$0/month**.
- `MEDIA_LOCAL_ROOT` defaults nowhere; `.env.example` uses `.local/media` (relative to the directory the API starts from, Git-ignored). Deleting that directory while PostgreSQL still has `encounter_media` rows leaves those photos unreadable (`404` on content); reset both together.
- Photos are private: only the owner can list them or read their bytes. Responses never contain a storage key, file path, device URI, EXIF, or location.

## 2. (Deferred) Configure Supabase email OTP

Skip this section during core development. It is required only for `AUTH_MODE=supabase`, which is deferred to the Integration & Pilot Hardening milestone. Set `AUTH_MODE=supabase` / `EXPO_PUBLIC_AUTH_MODE=supabase`, uncomment the Supabase block in `.env.local`, set `AUTH_ISSUER` to `https://PROJECT_REF.supabase.co/auth/v1`, `AUTH_JWKS_URL` to `https://PROJECT_REF.supabase.co/auth/v1/.well-known/jwks.json`, keep `AUTH_AUDIENCE=authenticated`, and copy the Project URL and **publishable** key (never a secret/service-role key) into the `EXPO_PUBLIC_SUPABASE_*` fields.

In the free Supabase project:

1. Authentication → Providers → Email: enable Email.
2. Authentication → Email Templates → Magic Link: make the message show `{{ .Token }}` so the user receives a numeric OTP instead of relying only on a magic link.
3. Authentication → Signing Keys: use an asymmetric signing key (RS256 for this baseline). The API deliberately accepts RS256 only.
4. Use the dashboard's built-in email sender for development. Its rate limits are acceptable for this requirement; configure custom SMTP only when justified later.

The mobile app calls `signInWithOtp` and then `verifyOtp(type: "email")`. It sends the resulting access JWT to the API; TOEM HIA never receives or stores a password.

## 3. Start PostgreSQL

Load the local environment in Bash/Git Bash and start the pinned image:

```bash
set -a
source .env.local
set +a
docker compose --env-file .env.local -f infra/docker/compose.yaml up -d

docker compose --env-file .env.local -f infra/docker/compose.yaml ps
```

Wait until `postgres` is `healthy`.

If you created the volume with the earlier stock `postgres:18.6-alpine` image, the data directory is compatible (same PostgreSQL 18.6); `docker compose ... up -d` recreates the container on the PostGIS image and keeps the data.

## 4. Run and roll back migrations

From the repository root with `.env.local` loaded:

```bash
go run ./services/api/cmd/migrate up          # apply all migrations (currently 000001–000003)
go run ./services/api/cmd/migrate down-to 2   # revert only the media migration
go run ./services/api/cmd/migrate up
go run ./services/api/cmd/migrate down-to 1   # revert media and wildlife; keep identity
go run ./services/api/cmd/migrate up
go run ./services/api/cmd/migrate down        # revert everything
```

There is no version table; every up script is idempotent, so re-running `up` is safe. `000001_identity` creates `users`, `auth_identities`, and `user_roles`. `000002_wildlife` enables PostGIS and creates `parks`, `zones`, `hias`, `encounters`, and the private `encounter_locations` table. `000003_media` creates the private `encounter_media` metadata table with integrity constraints. Rolling back `000003` leaves wildlife and identity data intact; rolling back `000002` leaves identity data and the PostGIS extension in place. Rollbacks that drop photo metadata do not remove the media files.

Load the development reference data (Lumpini Park with Lake Zone, North Path, South Pond; no Hias). It is idempotent and refuses any `APP_ENV` other than `development` or `test`:

```bash
go run ./services/api/cmd/seed
```

## 5. Start the API

```bash
go run ./services/api/cmd/api
```

In another terminal:

```bash
curl http://localhost:8080/health
curl http://localhost:8080/ready
```

Both return HTTP 200 when the process and database are healthy. Startup fails immediately if critical database/auth configuration is missing. In local mode the API logs a `local_development_auth_enabled` warning at startup.

Exercise local auth from the terminal (development only):

```bash
curl -X POST -H "Authorization: Bearer toem-hia-local-dev.developer-001" http://localhost:8080/v1/auth/bootstrap
```

Record an encounter as the development user (use a park/zone id from `GET /v1/parks` and `GET /v1/parks/{id}/zones`). The response never contains the location:

```bash
curl http://localhost:8080/v1/parks
curl -X POST -H "Authorization: Bearer toem-hia-local-dev.developer-001" -H "Content-Type: application/json" \
  -d '{"capturedAt":"2026-09-25T07:30:00+07:00","parkId":"<park-id>","zoneId":"<zone-id>","behavior":"BASKING","location":{"latitude":13.73,"longitude":100.54,"accuracyMeters":5,"source":"GPS"}}' \
  http://localhost:8080/v1/encounters
curl -X POST -H "Authorization: Bearer toem-hia-local-dev.developer-001" http://localhost:8080/v1/encounters/<encounter-id>/submit
```

## 6. Start the mobile app

Expo embeds variables prefixed `EXPO_PUBLIC_` from the process environment. From the same shell where `.env.local` is loaded:

```bash
cd apps/mobile
npm ci
npm start
```

Scan the QR code with Expo Go, or press `a`/`i` for a configured simulator.

Local-auth smoke test (the development runtime acceptance test):

1. Launch → Splash → guest Home.
2. **Create Your Hia Passport** → the screen shows a `LOCAL DEVELOPMENT MODE` badge and **Continue as Dev User** (no email form).
3. **Continue as Dev User** → `POST /v1/auth/bootstrap` creates (first time) or reuses the internal user → Passport Setup.
4. Enter username and display name → Profile shows the internal user and USER role.
5. **Log out** → local session cleared from SecureStore → guest Home.
6. Repeat step 3: the same internal user is returned; no duplicate is created.

In Supabase mode (deferred) the same screen shows the email/OTP form instead and never shows **Continue as Dev User**. Supabase token auto-refresh starts only while React Native reports the app as active; backgrounding stops refresh, and provider unmount removes the AppState listener.

Camera smoke test (Requirement 003; needs a real device or a simulator with a camera, and the API running with `MEDIA_MODE=local`):

1. Home → **Scan a Hia** → allow the camera → the guidance and **Observe. Don’t Disturb.** are shown.
2. Take a photo → preview with **Retake** and **Save Encounter**. Retake returns to the camera.
3. As a guest, **Save Encounter** → **Create Your Hia Passport** → **Continue as Dev User** (→ Passport Setup the first time) → back on the same preview; the save resumes and ends at **Encounter saved**.
4. Stop the API, take and save another photo → **Retry** is offered and the photo is kept; start the API, **Retry** → saved, and only one encounter exists.

**DEVICE VALIDATION — NOT RUN.** Automated tests mock Expo Camera and FileSystem; the steps above have not been run on real camera hardware. They are mandatory before any public or user pilot.

## 7. Run checks

Backend unit tests (integration tests skip only when `TEST_DATABASE_URL` is absent):

```bash
cd services/api
gofmt -w .
go vet ./...
go test ./...
```

PostgreSQL integration/migration tests from the repository root:

```bash
set -a; source .env.local; set +a
export TEST_DATABASE_URL="$DATABASE_URL"
cd services/api
go test -race -count=1 ./...
```

Mobile and contracts:

```bash
cd apps/mobile
npm ci
npm test
npm run typecheck
npm run lint
npx expo install --check
npx --yes expo-doctor@1.20.4
npm audit --audit-level=high
./node_modules/.bin/tsc -p ../../packages/contracts/tsconfig.json
```

GitHub Actions runs the same gates with the same pinned PostgreSQL 18.6 + PostGIS 3.6.4 service image and pinned Node/Go versions.

## API

- `GET /health`
- `GET /ready`
- `POST /v1/auth/bootstrap` (Bearer credential: Supabase JWT, or the local dev credential in `AUTH_MODE=local`)
- `GET /v1/users/me` (Bearer credential)
- `PATCH /v1/users/me` (Bearer credential; only `username`, `displayName`, `locale`)
- `GET /v1/parks`, `GET /v1/parks/{id}`, `GET /v1/parks/{id}/zones` (public; ACTIVE only)
- `GET /v1/hias[?parkId=]`, `GET /v1/hias/{publicCode}` (public; read-only)
- `POST /v1/encounters`, `GET|PATCH /v1/encounters/{id}`, `POST /v1/encounters/{id}/submit`, `GET /v1/users/me/encounters` (Bearer credential; owner-only; writes require an `ACTIVE` user)
- `POST /v1/encounters/{id}/media` (Bearer; raw JPEG/PNG body with matching `Content-Type`, ≤ 15 MiB, DRAFT encounters only, ≤ 5 photos; `201` new, `200` same bytes retried), `GET /v1/encounters/{id}/media`, `GET /v1/media/{id}/content` (Bearer; owner-only)

See `packages/contracts/openapi.yaml`. Errors always use:

```json
{"error":{"code":"UNAUTHENTICATED","message":"Authentication is required","requestId":"..."}}
```

## Security notes

- Local development auth is guarded three times on the API (config load, config re-validation in `identity.FromConfig`, `NewLocalDevVerifier` constructor) and twice on mobile (`EXPO_PUBLIC_APP_ENV` allowlist and `__DEV__` bundle check). Each guard has a test, including a real-process test that `APP_ENV=production AUTH_MODE=local` exits non-zero before touching the database.
- Supabase session data and the local development credential use Expo SecureStore, never AsyncStorage.
- The API verifies RS256 signature, key ID, issuer, audience, expiry, issued-at validity, and subject.
- JWKS refreshes are throttled and the key cache is TTL-bounded, so unauthenticated callers cannot turn unknown-key-id tokens into unbounded outbound fetches against the identity provider. Signing keys below a 2048-bit RSA modulus are ignored even if the JWKS endpoint offers them. Key rotation is still picked up once the cache expires.
- Client claims do not authorize internal user IDs or roles.
- SQL is parameterized and identity creation is one transaction.
- Logs contain request ID, method, path, status, and duration, but not JWT, OTP, email, credentials, or precise location. Encounter events log only request ID, encounter ID, user ID, park ID, and status.
- Precise encounter location is stored in a separate private table and is write-only through the API; responses expose park/zone only (ADR-008). Encounter writes are owner-only, server-derived, and allowed only for `ACTIVE` users.
- Photos (ADR-010): the request body is capped at 15 MiB before it is read into anything and is streamed to a staging file; the real format is sniffed from the bytes and must match `Content-Type`; width/height are read from the header and bounded (12000 px per side, 50 MP) before any full decode; a full decode must then succeed. Storage keys are server-generated. Logs and responses never contain paths, storage keys, image bytes, EXIF, or location.
- React and React DOM are pinned to Expo SDK 57's supported 19.2.3 baseline. Requirement 001 does not use React Server Components, so `react-server-dom-webpack` is not a direct dependency. React and React DOM have no `expo.install.exclude` exception; `npx expo install --check` validates them normally.
- `govulncheck` reports **no vulnerabilities**. `pgx` is pinned to v5.9.2 and `golang.org/x/text` to v0.39.0 specifically to clear GO-2026-5004 (SQL injection via dollar-quoted placeholder confusion) and GO-2026-5970.
- `npm audit --audit-level=high` passes. Thirteen **moderate** advisories remain inside Expo's own build toolchain (`@expo/cli` → `xcode` → `uuid`, and `expo-router` → `query-string` → `decode-uri-component`). `npm audit fix --force` "resolves" them by downgrading to Expo 46 / expo-router 5, which would abandon the SDK 57 baseline, so they are accepted and gated at `high` instead. They affect developer tooling, not the shipped app runtime.

## Current limits

There is no deployed API/database, production SMTP, Apple/Google/LINE login, account-deletion workflow, production media store, Re-ID, maps, or notifications. The mobile app has Scan a Hia (camera capture, preview, private photo upload) but no HiaDex, Explore, or identification results. Local media storage is development/test only (ADR-010). Supabase Auth, Email OTP, and real provider JWT verification end to end are **DEFERRED TO INTEGRATION & PILOT HARDENING** and have not been tested against a real project; they are mandatory before any public beta or Lumpini pilot. Account deletion must be designed with future wildlife contribution-retention semantics before public beta or store release.
