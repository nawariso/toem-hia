package http

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"log/slog"
	nethttp "net/http"
	"strings"
	"time"

	"github.com/nawariso/toem-hia/services/api/internal/application"
	"github.com/nawariso/toem-hia/services/api/internal/domain"
)

type Server struct {
	users      application.Users
	verifier   application.IdentityVerifier
	ready      application.Readiness
	parks      application.Parks
	hias       application.Hias
	encounters application.Encounters
	media      application.EncounterMedia
	logger     *slog.Logger
	mux        *nethttp.ServeMux
}
type identityKey struct{}
type requestIDKey struct{}
type errorBody struct {
	Error struct {
		Code      string `json:"code"`
		Message   string `json:"message"`
		RequestID string `json:"requestId"`
	} `json:"error"`
}
type userResponse struct {
	ID              string   `json:"id"`
	Username        *string  `json:"username"`
	DisplayName     *string  `json:"displayName"`
	AvatarURL       *string  `json:"avatarUrl"`
	Locale          string   `json:"locale"`
	Roles           []string `json:"roles"`
	ProfileComplete bool     `json:"profileComplete"`
}

// Option customizes server construction without widening the constructor.
type Option func(*Server)

// WithLogger replaces the structured logger used for request logs.
func WithLogger(logger *slog.Logger) Option {
	return func(s *Server) {
		if logger != nil {
			s.logger = logger
		}
	}
}

func NewServer(users application.Users, verifier application.IdentityVerifier, ready application.Readiness, options ...Option) *Server {
	s := &Server{users: users, verifier: verifier, ready: ready, logger: slog.Default(), mux: nethttp.NewServeMux()}
	for _, option := range options {
		option(s)
	}
	s.routes()
	return s
}

// Handler assigns the request ID first so that request logs, response headers,
// and error bodies all reference the same identifier.
func (s *Server) Handler() nethttp.Handler { return s.requestID(s.logging(s.mux)) }
func (s *Server) routes() {
	s.mux.HandleFunc("GET /health", s.health)
	s.mux.HandleFunc("GET /ready", s.readiness)
	s.mux.Handle("POST /v1/auth/bootstrap", s.auth(nethttp.HandlerFunc(s.bootstrap)))
	s.mux.Handle("GET /v1/users/me", s.auth(nethttp.HandlerFunc(s.me)))
	s.mux.Handle("PATCH /v1/users/me", s.auth(nethttp.HandlerFunc(s.update)))
	s.wildlifeRoutes()
	s.mediaRoutes()
}
func (s *Server) health(w nethttp.ResponseWriter, _ *nethttp.Request) {
	writeJSON(w, nethttp.StatusOK, map[string]string{"status": "ok"})
}
func (s *Server) readiness(w nethttp.ResponseWriter, r *nethttp.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	if err := s.ready.Ping(ctx); err != nil {
		writeError(w, r, nethttp.StatusServiceUnavailable, "NOT_READY", "Service is not ready")
		return
	}
	writeJSON(w, nethttp.StatusOK, map[string]string{"status": "ready"})
}
func (s *Server) auth(next nethttp.Handler) nethttp.Handler {
	return nethttp.HandlerFunc(func(w nethttp.ResponseWriter, r *nethttp.Request) {
		parts := strings.SplitN(r.Header.Get("Authorization"), " ", 2)
		if len(parts) != 2 || !strings.EqualFold(parts[0], "Bearer") || strings.TrimSpace(parts[1]) == "" {
			writeError(w, r, nethttp.StatusUnauthorized, "UNAUTHENTICATED", "Authentication is required")
			return
		}
		identity, err := s.verifier.Verify(r.Context(), parts[1])
		if err != nil {
			writeError(w, r, nethttp.StatusUnauthorized, "UNAUTHENTICATED", "Authentication token is invalid or expired")
			return
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), identityKey{}, identity)))
	})
}
func getIdentity(r *nethttp.Request) domain.ExternalIdentity {
	return r.Context().Value(identityKey{}).(domain.ExternalIdentity)
}
func (s *Server) bootstrap(w nethttp.ResponseWriter, r *nethttp.Request) {
	u, err := s.users.Bootstrap(r.Context(), getIdentity(r))
	s.respondUser(w, r, u, err)
}
func (s *Server) me(w nethttp.ResponseWriter, r *nethttp.Request) {
	u, err := s.users.Current(r.Context(), getIdentity(r))
	s.respondUser(w, r, u, err)
}
func (s *Server) update(w nethttp.ResponseWriter, r *nethttp.Request) {
	r.Body = nethttp.MaxBytesReader(w, r.Body, 1<<20)
	defer r.Body.Close()
	var input struct {
		Username    *string `json:"username"`
		DisplayName *string `json:"displayName"`
		Locale      *string `json:"locale"`
	}
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil {
		writeError(w, r, nethttp.StatusBadRequest, "INVALID_REQUEST", "Request body is invalid")
		return
	}
	var extra any
	if decoder.Decode(&extra) == nil {
		writeError(w, r, nethttp.StatusBadRequest, "INVALID_REQUEST", "Request body must contain one JSON object")
		return
	}
	u, err := s.users.UpdateProfile(r.Context(), getIdentity(r), domain.ProfilePatch{Username: input.Username, DisplayName: input.DisplayName, Locale: input.Locale})
	s.respondUser(w, r, u, err)
}
func (s *Server) respondUser(w nethttp.ResponseWriter, r *nethttp.Request, u domain.User, err error) {
	if err != nil {
		switch {
		case errors.Is(err, application.ErrNotFound):
			writeError(w, r, nethttp.StatusNotFound, "USER_NOT_FOUND", "User was not found")
		case errors.Is(err, domain.ErrUsernameTaken):
			writeError(w, r, nethttp.StatusConflict, "USERNAME_TAKEN", "Username is already taken")
		case errors.Is(err, domain.ErrInvalidUsername), errors.Is(err, domain.ErrInvalidDisplayName), errors.Is(err, domain.ErrInvalidLocale):
			writeError(w, r, nethttp.StatusBadRequest, "VALIDATION_ERROR", err.Error())
		default:
			writeError(w, r, nethttp.StatusInternalServerError, "INTERNAL_ERROR", "An unexpected error occurred")
		}
		return
	}
	writeJSON(w, nethttp.StatusOK, userResponse{ID: u.ID, Username: u.Username, DisplayName: u.DisplayName, AvatarURL: u.AvatarURL, Locale: u.Locale, Roles: u.Roles, ProfileComplete: u.ProfileComplete()})
}
func (s *Server) requestID(next nethttp.Handler) nethttp.Handler {
	return nethttp.HandlerFunc(func(w nethttp.ResponseWriter, r *nethttp.Request) {
		id := newRequestID()
		w.Header().Set("X-Request-ID", id)
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), requestIDKey{}, id)))
	})
}

type statusWriter struct {
	nethttp.ResponseWriter
	status int
}

func (w *statusWriter) WriteHeader(code int) { w.status = code; w.ResponseWriter.WriteHeader(code) }
func (s *Server) logging(next nethttp.Handler) nethttp.Handler {
	return nethttp.HandlerFunc(func(w nethttp.ResponseWriter, r *nethttp.Request) {
		start := time.Now()
		sw := &statusWriter{ResponseWriter: w, status: 200}
		next.ServeHTTP(sw, r)
		s.logger.Info("http_request", "request_id", requestID(r), "method", r.Method, "path", r.URL.Path, "status", sw.status, "duration_ms", time.Since(start).Milliseconds())
	})
}
func newRequestID() string {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "unavailable"
	}
	return hex.EncodeToString(b)
}
func requestID(r *nethttp.Request) string {
	id, _ := r.Context().Value(requestIDKey{}).(string)
	return id
}
func writeJSON(w nethttp.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
func writeError(w nethttp.ResponseWriter, r *nethttp.Request, status int, code, message string) {
	body := errorBody{}
	body.Error.Code = code
	body.Error.Message = message
	body.Error.RequestID = requestID(r)
	writeJSON(w, status, body)
}
