package http

import (
	"errors"
	"io"
	nethttp "net/http"
	"strconv"
	"time"

	"github.com/nawariso/toem-hia/services/api/internal/application"
	"github.com/nawariso/toem-hia/services/api/internal/domain"
	"github.com/nawariso/toem-hia/services/api/internal/domain/encounter"
	"github.com/nawariso/toem-hia/services/api/internal/domain/media"
)

// WithMedia enables the Requirement 003 private photo routes. Without it
// (MEDIA_MODE=disabled) the same routes exist but answer 503
// MEDIA_UNAVAILABLE, so clients see an explicit state instead of a 404.
func WithMedia(svc application.EncounterMedia) Option {
	return func(s *Server) { s.media = svc }
}

func (s *Server) mediaRoutes() {
	if s.encounters == nil {
		return
	}
	upload, list, content := s.uploadMedia, s.listMedia, s.mediaContent
	if s.media == nil {
		upload, list, content = s.mediaUnavailable, s.mediaUnavailable, s.mediaUnavailable
	}
	s.mux.Handle("POST /v1/encounters/{id}/media", s.auth(nethttp.HandlerFunc(upload)))
	s.mux.Handle("GET /v1/encounters/{id}/media", s.auth(nethttp.HandlerFunc(list)))
	s.mux.Handle("GET /v1/media/{id}/content", s.auth(nethttp.HandlerFunc(content)))
}

// mediaResponse is an explicit allowlist. It has no storage key, filesystem
// path, device URI, EXIF, or location field. Every Requirement 003 photo is
// PRIVATE; the field makes that visible in the contract.
type mediaResponse struct {
	ID          string    `json:"id"`
	EncounterID string    `json:"encounterId"`
	Kind        string    `json:"kind"`
	Status      string    `json:"status"`
	Visibility  string    `json:"visibility"`
	ContentType string    `json:"contentType"`
	ByteSize    int64     `json:"byteSize"`
	Width       int       `json:"width"`
	Height      int       `json:"height"`
	SHA256      string    `json:"sha256"`
	CreatedAt   time.Time `json:"createdAt"`
}

const visibilityPrivate = "PRIVATE"

func toMedia(m media.Media) mediaResponse {
	return mediaResponse{
		ID: m.ID, EncounterID: m.EncounterID, Kind: m.Kind, Status: m.Status, Visibility: visibilityPrivate,
		ContentType: m.ContentType, ByteSize: m.ByteSize, Width: m.Width, Height: m.Height,
		SHA256: m.SHA256, CreatedAt: m.CreatedAt.UTC(),
	}
}

// privateNoStore marks a response as personal data that no shared or
// browser cache may keep.
func privateNoStore(w nethttp.ResponseWriter) {
	w.Header().Set("Cache-Control", "private, no-store")
	w.Header().Set("Pragma", "no-cache")
}

// uploadMedia takes the image itself as the request body, with
// Content-Type image/jpeg or image/png. There is no filename, path, or
// multipart envelope to trust or sanitise; the declared type is only compared
// with the sniffed content.
func (s *Server) uploadMedia(w nethttp.ResponseWriter, r *nethttp.Request) {
	privateNoStore(w)
	if r.ContentLength > media.MaxBytes {
		s.mediaError(w, r, media.ErrTooLarge)
		return
	}
	r.Body = nethttp.MaxBytesReader(w, r.Body, media.MaxBytes+1)
	defer r.Body.Close()
	result, err := s.media.Upload(r.Context(), getIdentity(r), r.PathValue("id"), r.Header.Get("Content-Type"), r.Body)
	if err != nil {
		s.mediaError(w, r, err)
		return
	}
	status := nethttp.StatusOK
	if result.Created {
		status = nethttp.StatusCreated
		s.logMedia(r, "media_uploaded", result.UserID, result.Media)
	}
	writeJSON(w, status, toMedia(result.Media))
}

func (s *Server) listMedia(w nethttp.ResponseWriter, r *nethttp.Request) {
	privateNoStore(w)
	items, err := s.media.List(r.Context(), getIdentity(r), r.PathValue("id"))
	if err != nil {
		s.mediaError(w, r, err)
		return
	}
	out := listResponse[mediaResponse]{Items: make([]mediaResponse, 0, len(items))}
	for _, m := range items {
		out.Items = append(out.Items, toMedia(m))
	}
	writeJSON(w, nethttp.StatusOK, out)
}

func (s *Server) mediaContent(w nethttp.ResponseWriter, r *nethttp.Request) {
	privateNoStore(w)
	m, body, err := s.media.Content(r.Context(), getIdentity(r), r.PathValue("id"))
	if err != nil {
		s.mediaError(w, r, err)
		return
	}
	defer body.Close()
	h := w.Header()
	h.Set("Content-Type", m.ContentType)
	h.Set("Content-Length", strconv.FormatInt(m.ByteSize, 10))
	h.Set("Content-Disposition", "inline")
	h.Set("X-Content-Type-Options", "nosniff")
	h.Set("Content-Security-Policy", "default-src 'none'; sandbox")
	w.WriteHeader(nethttp.StatusOK)
	if _, err = io.Copy(w, body); err != nil {
		// Headers are already sent; record the failure without details.
		s.logger.Error("media_content_failed", "request_id", requestID(r), "media_id", m.ID, "error_type", "stream")
	}
}

func (s *Server) mediaUnavailable(w nethttp.ResponseWriter, r *nethttp.Request) {
	privateNoStore(w)
	writeError(w, r, nethttp.StatusServiceUnavailable, "MEDIA_UNAVAILABLE", "Photo storage is not enabled on this server")
}

// logMedia writes only the fields Requirement 003 allows: never image bytes,
// storage keys, filesystem paths, device URIs, credentials, email, EXIF,
// coordinates, or notes.
func (s *Server) logMedia(r *nethttp.Request, event, userID string, m media.Media) {
	s.logger.Info(event, "request_id", requestID(r), "media_id", m.ID, "encounter_id", m.EncounterID,
		"user_id", userID, "byte_size", m.ByteSize, "status", m.Status)
}

// mediaError maps media errors to the stable error contract and defers every
// other error to wildlifeError (ownership, user state, encounter state).
func (s *Server) mediaError(w nethttp.ResponseWriter, r *nethttp.Request, err error) {
	var tooLarge *nethttp.MaxBytesError
	switch {
	case errors.Is(err, media.ErrTooLarge), errors.As(err, &tooLarge):
		writeError(w, r, nethttp.StatusRequestEntityTooLarge, "MEDIA_TOO_LARGE", media.ErrTooLarge.Error())
	case errors.Is(err, media.ErrUnsupportedType), errors.Is(err, media.ErrTypeMismatch):
		writeError(w, r, nethttp.StatusUnsupportedMediaType, "UNSUPPORTED_MEDIA_TYPE", err.Error())
	case errors.Is(err, media.ErrInvalidImage):
		writeError(w, r, nethttp.StatusBadRequest, "INVALID_IMAGE", err.Error())
	case errors.Is(err, media.ErrDimensions):
		writeError(w, r, nethttp.StatusBadRequest, "IMAGE_DIMENSIONS_EXCEEDED", err.Error())
	case errors.Is(err, media.ErrTooManyPhotos):
		writeError(w, r, nethttp.StatusConflict, "MEDIA_LIMIT_REACHED", err.Error())
	case errors.Is(err, media.ErrNotFound):
		writeError(w, r, nethttp.StatusNotFound, "MEDIA_NOT_FOUND", "Photo was not found")
	case errors.Is(err, domain.ErrUserNotActive):
		writeError(w, r, nethttp.StatusForbidden, "USER_NOT_ACTIVE", "Only active users can add photos")
	case errors.Is(err, encounter.ErrNotEditable):
		writeError(w, r, nethttp.StatusConflict, "ENCOUNTER_NOT_EDITABLE", "Photos can only be added while the encounter is a draft")
	default:
		s.wildlifeError(w, r, err)
	}
}
