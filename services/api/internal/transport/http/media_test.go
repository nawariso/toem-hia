package http_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/nawariso/toem-hia/services/api/internal/application"
	"github.com/nawariso/toem-hia/services/api/internal/domain"
	"github.com/nawariso/toem-hia/services/api/internal/domain/encounter"
	"github.com/nawariso/toem-hia/services/api/internal/domain/media"
	httptransport "github.com/nawariso/toem-hia/services/api/internal/transport/http"
)

// stubEncounters satisfies application.Encounters; media routes are only
// registered alongside the wildlife routes.
type stubEncounters struct{ application.Encounters }

type stubParks struct{ application.Parks }
type stubHias struct{ application.Hias }

const (
	encounterID = "5b0d2c55-6d57-4a55-9a8e-8b5d1f0e1a11"
	mediaID     = "0f8fad5b-d9cb-469f-a165-70867728950e"
	storageKey  = "photos/0f8fad5b-d9cb-469f-a165-70867728950e.jpg"
)

var photo = media.Media{
	ID: mediaID, EncounterID: encounterID, Kind: media.KindPhoto, Status: media.StatusReady,
	ContentType: media.ContentTypeJPEG, ByteSize: 5, Width: 4, Height: 3,
	SHA256: strings.Repeat("a", 64), StorageKey: storageKey, CreatedAt: time.Date(2026, 9, 26, 10, 0, 0, 0, time.UTC),
}

type stubMedia struct {
	err     error
	created bool
	gotType string
	gotBody []byte
}

func (s *stubMedia) Upload(_ context.Context, _ domain.ExternalIdentity, _ string, contentType string, body io.Reader) (application.MediaUpload, error) {
	s.gotType = contentType
	s.gotBody, _ = io.ReadAll(body)
	if s.err != nil {
		return application.MediaUpload{}, s.err
	}
	return application.MediaUpload{Media: photo, UserID: "user-1", Created: s.created}, nil
}

func (s *stubMedia) List(context.Context, domain.ExternalIdentity, string) ([]media.Media, error) {
	if s.err != nil {
		return nil, s.err
	}
	return []media.Media{photo}, nil
}

func (s *stubMedia) Content(context.Context, domain.ExternalIdentity, string) (media.Media, io.ReadCloser, error) {
	if s.err != nil {
		return media.Media{}, nil, s.err
	}
	return photo, io.NopCloser(strings.NewReader("bytes")), nil
}

func mediaServer(t *testing.T, svc application.EncounterMedia) (http.Handler, *bytes.Buffer) {
	t.Helper()
	logs := &bytes.Buffer{}
	options := []httptransport.Option{
		httptransport.WithLogger(slog.New(slog.NewJSONHandler(logs, nil))),
		httptransport.WithWildlife(stubParks{}, stubHias{}, stubEncounters{}),
	}
	if svc != nil {
		options = append(options, httptransport.WithMedia(svc))
	}
	user := domain.User{ID: "user-1", Locale: "th", Status: domain.UserStatusActive, Roles: []string{"USER"}}
	return httptransport.NewServer(app{user: user}, verifier{}, ready{}, options...).Handler(), logs
}

func do(h http.Handler, method, path, contentType string, body io.Reader, authed bool) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, body)
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	if authed {
		req.Header.Set("Authorization", "Bearer valid")
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func errorCode(t *testing.T, rec *httptest.ResponseRecorder) string {
	t.Helper()
	var body struct {
		Error struct{ Code string } `json:"error"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("error body is not JSON: %q", rec.Body.String())
	}
	return body.Error.Code
}

func assertPrivateNoStore(t *testing.T, rec *httptest.ResponseRecorder) {
	t.Helper()
	if got := rec.Header().Get("Cache-Control"); got != "private, no-store" {
		t.Fatalf("Cache-Control = %q, want private, no-store", got)
	}
}

func TestMediaRoutesRequireAuthentication(t *testing.T) {
	h, _ := mediaServer(t, &stubMedia{})
	for _, r := range []struct{ method, path string }{
		{http.MethodPost, "/v1/encounters/" + encounterID + "/media"},
		{http.MethodGet, "/v1/encounters/" + encounterID + "/media"},
		{http.MethodGet, "/v1/media/" + mediaID + "/content"},
	} {
		if rec := do(h, r.method, r.path, media.ContentTypeJPEG, strings.NewReader("x"), false); rec.Code != http.StatusUnauthorized {
			t.Fatalf("%s %s without a token: want 401, got %d", r.method, r.path, rec.Code)
		}
	}
}

func TestUploadResponseIsAnAllowlistWithoutStorageDetails(t *testing.T) {
	svc := &stubMedia{created: true}
	h, logs := mediaServer(t, svc)
	rec := do(h, http.MethodPost, "/v1/encounters/"+encounterID+"/media", "image/jpeg", strings.NewReader("image-bytes"), true)
	if rec.Code != http.StatusCreated {
		t.Fatalf("want 201, got %d %s", rec.Code, rec.Body.String())
	}
	assertPrivateNoStore(t, rec)
	if svc.gotType != "image/jpeg" || string(svc.gotBody) != "image-bytes" {
		t.Fatalf("service did not receive the raw body and declared type: %q %q", svc.gotType, svc.gotBody)
	}
	var body map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	want := map[string]bool{"id": true, "encounterId": true, "kind": true, "status": true, "visibility": true,
		"contentType": true, "byteSize": true, "width": true, "height": true, "sha256": true, "createdAt": true}
	for key := range body {
		if !want[key] {
			t.Fatalf("response carries a field outside the allowlist: %q", key)
		}
	}
	if body["visibility"] != "PRIVATE" || body["id"] != mediaID {
		t.Fatalf("unexpected response: %v", body)
	}
	raw := rec.Body.String()
	for _, leak := range []string{storageKey, "photos/", "storage", "path", "uri", "exif", "latitude", "longitude"} {
		if strings.Contains(strings.ToLower(raw), strings.ToLower(leak)) {
			t.Fatalf("response leaked %q: %s", leak, raw)
		}
	}
	logged := logs.String()
	for _, leak := range []string{storageKey, "photos/", "image-bytes", "Bearer", "latitude", "notes"} {
		if strings.Contains(logged, leak) {
			t.Fatalf("log leaked %q: %s", leak, logged)
		}
	}
	for _, field := range []string{`"media_uploaded"`, `"media_id":"` + mediaID, `"encounter_id":"` + encounterID, `"user_id":"user-1"`, `"byte_size":5`, `"status":"READY"`} {
		if !strings.Contains(logged, field) {
			t.Fatalf("media log is missing %s: %s", field, logged)
		}
	}
}

func TestIdempotentRetryAnswers200(t *testing.T) {
	h, logs := mediaServer(t, &stubMedia{created: false})
	rec := do(h, http.MethodPost, "/v1/encounters/"+encounterID+"/media", "image/jpeg", strings.NewReader("x"), true)
	if rec.Code != http.StatusOK {
		t.Fatalf("a retried upload must answer 200, got %d", rec.Code)
	}
	if strings.Contains(logs.String(), "media_uploaded") {
		t.Fatal("a retried upload must not be logged as a new upload")
	}
}

func TestUploadErrorsMapToStableCodes(t *testing.T) {
	cases := []struct {
		err    error
		status int
		code   string
	}{
		{media.ErrTooLarge, http.StatusRequestEntityTooLarge, "MEDIA_TOO_LARGE"},
		{media.ErrUnsupportedType, http.StatusUnsupportedMediaType, "UNSUPPORTED_MEDIA_TYPE"},
		{media.ErrTypeMismatch, http.StatusUnsupportedMediaType, "UNSUPPORTED_MEDIA_TYPE"},
		{media.ErrInvalidImage, http.StatusBadRequest, "INVALID_IMAGE"},
		{media.ErrDimensions, http.StatusBadRequest, "IMAGE_DIMENSIONS_EXCEEDED"},
		{media.ErrTooManyPhotos, http.StatusConflict, "MEDIA_LIMIT_REACHED"},
		{encounter.ErrNotFound, http.StatusNotFound, "ENCOUNTER_NOT_FOUND"},
		{encounter.ErrNotEditable, http.StatusConflict, "ENCOUNTER_NOT_EDITABLE"},
		{domain.ErrUserNotActive, http.StatusForbidden, "USER_NOT_ACTIVE"},
		{application.ErrUserNotBootstrapped, http.StatusForbidden, "USER_NOT_BOOTSTRAPPED"},
		{errors.New("open /srv/secret/media/photos/x.jpg: disk failure"), http.StatusInternalServerError, "INTERNAL_ERROR"},
	}
	for _, tc := range cases {
		t.Run(tc.code, func(t *testing.T) {
			h, logs := mediaServer(t, &stubMedia{err: tc.err})
			rec := do(h, http.MethodPost, "/v1/encounters/"+encounterID+"/media", "image/jpeg", strings.NewReader("x"), true)
			if rec.Code != tc.status || errorCode(t, rec) != tc.code {
				t.Fatalf("want %d %s, got %d %s", tc.status, tc.code, rec.Code, rec.Body.String())
			}
			for _, leak := range []string{"/srv/secret", "disk failure"} {
				if strings.Contains(rec.Body.String(), leak) || strings.Contains(logs.String(), leak) {
					t.Fatalf("internal detail %q leaked", leak)
				}
			}
		})
	}
}

func TestDeclaredOversizeIsRefusedBeforeReadingTheBody(t *testing.T) {
	svc := &stubMedia{created: true}
	h, _ := mediaServer(t, svc)
	req := httptest.NewRequest(http.MethodPost, "/v1/encounters/"+encounterID+"/media", strings.NewReader("x"))
	req.Header.Set("Content-Type", "image/jpeg")
	req.Header.Set("Authorization", "Bearer valid")
	req.ContentLength = media.MaxBytes + 1
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusRequestEntityTooLarge || errorCode(t, rec) != "MEDIA_TOO_LARGE" {
		t.Fatalf("want 413 MEDIA_TOO_LARGE, got %d %s", rec.Code, rec.Body.String())
	}
	if svc.gotBody != nil {
		t.Fatal("the service must not be called for a declared oversized body")
	}
}

func TestContentIsPrivateAndTyped(t *testing.T) {
	h, _ := mediaServer(t, &stubMedia{})
	rec := do(h, http.MethodGet, "/v1/media/"+mediaID+"/content", "", nil, true)
	if rec.Code != http.StatusOK || rec.Body.String() != "bytes" {
		t.Fatalf("want 200 with the bytes, got %d %q", rec.Code, rec.Body.String())
	}
	assertPrivateNoStore(t, rec)
	h2 := rec.Header()
	if h2.Get("Content-Type") != media.ContentTypeJPEG || h2.Get("X-Content-Type-Options") != "nosniff" ||
		h2.Get("Content-Length") != "5" || !strings.Contains(h2.Get("Content-Security-Policy"), "sandbox") {
		t.Fatalf("unexpected content headers: %v", h2)
	}
	if strings.Contains(h2.Get("Content-Disposition"), "filename") {
		t.Fatal("content must not carry a filename")
	}
}

func TestOtherUsersMediaIsNotFound(t *testing.T) {
	h, _ := mediaServer(t, &stubMedia{err: media.ErrNotFound})
	rec := do(h, http.MethodGet, "/v1/media/"+mediaID+"/content", "", nil, true)
	if rec.Code != http.StatusNotFound || errorCode(t, rec) != "MEDIA_NOT_FOUND" {
		t.Fatalf("want 404 MEDIA_NOT_FOUND, got %d %s", rec.Code, rec.Body.String())
	}
	assertPrivateNoStore(t, rec)
}

func TestListIsPrivate(t *testing.T) {
	h, _ := mediaServer(t, &stubMedia{})
	rec := do(h, http.MethodGet, "/v1/encounters/"+encounterID+"/media", "", nil, true)
	if rec.Code != http.StatusOK {
		t.Fatalf("want 200, got %d", rec.Code)
	}
	assertPrivateNoStore(t, rec)
	var body struct {
		Items []map[string]any `json:"items"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil || len(body.Items) != 1 {
		t.Fatalf("unexpected list body: %s", rec.Body.String())
	}
	if strings.Contains(rec.Body.String(), "photos/") {
		t.Fatal("list leaked a storage key")
	}
}

// MEDIA_MODE=disabled: routes exist and answer an explicit 503, after
// authentication, instead of a misleading 404.
func TestDisabledMediaAnswers503(t *testing.T) {
	h, _ := mediaServer(t, nil)
	for _, r := range []struct{ method, path string }{
		{http.MethodPost, "/v1/encounters/" + encounterID + "/media"},
		{http.MethodGet, "/v1/encounters/" + encounterID + "/media"},
		{http.MethodGet, "/v1/media/" + mediaID + "/content"},
	} {
		rec := do(h, r.method, r.path, "image/jpeg", strings.NewReader("x"), true)
		if rec.Code != http.StatusServiceUnavailable || errorCode(t, rec) != "MEDIA_UNAVAILABLE" {
			t.Fatalf("%s %s: want 503 MEDIA_UNAVAILABLE, got %d %s", r.method, r.path, rec.Code, rec.Body.String())
		}
		if rec := do(h, r.method, r.path, "image/jpeg", strings.NewReader("x"), false); rec.Code != http.StatusUnauthorized {
			t.Fatalf("%s %s: disabled media must still require authentication, got %d", r.method, r.path, rec.Code)
		}
	}
}
