package config

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// Application environments. APP_ENV has no default: an unset value must never
// be read as "development", because development permits local authentication.
const (
	EnvDevelopment = "development"
	EnvTest        = "test"
	EnvProduction  = "production"
)

// Authentication modes. There is no implicit fallback between them.
const (
	AuthModeLocal    = "local"
	AuthModeSupabase = "supabase"
)

// Media modes (Requirement 003). MEDIA_MODE has no default either:
//   - local    stores private photos on this machine's filesystem under
//     MEDIA_LOCAL_ROOT. Development and test only.
//   - disabled runs the API without a media store; media endpoints answer
//     503 MEDIA_UNAVAILABLE. This is the only valid production value until a
//     production object-storage adapter exists.
const (
	MediaModeLocal    = "local"
	MediaModeDisabled = "disabled"
)

// ErrLocalAuthForbidden is returned when local development authentication is
// requested outside an environment on the local-auth allowlist.
var ErrLocalAuthForbidden = errors.New("AUTH_MODE=local is development-only and is forbidden unless APP_ENV is development or test")

// ErrLocalMediaForbidden is returned when local filesystem media storage is
// requested outside development/test. Production must never silently keep
// private wildlife photos on an arbitrary local disk.
var ErrLocalMediaForbidden = errors.New("MEDIA_MODE=local is development-only and is forbidden unless APP_ENV is development or test")

type Config struct {
	AppEnv, AuthMode, HTTPPort, DatabaseURL string
	AuthIssuer, AuthAudience, AuthJWKSURL   string
	MediaMode, MediaLocalRoot               string
}

// Load reads configuration from the environment and fails fast. Supabase
// verification settings are required only when AUTH_MODE=supabase, and
// MEDIA_LOCAL_ROOT only when MEDIA_MODE=local.
func Load() (Config, error) {
	c := Config{
		AppEnv:         strings.TrimSpace(os.Getenv("APP_ENV")),
		AuthMode:       strings.TrimSpace(os.Getenv("AUTH_MODE")),
		HTTPPort:       get("HTTP_PORT", "8080"),
		DatabaseURL:    os.Getenv("DATABASE_URL"),
		AuthIssuer:     os.Getenv("AUTH_ISSUER"),
		AuthAudience:   os.Getenv("AUTH_AUDIENCE"),
		AuthJWKSURL:    os.Getenv("AUTH_JWKS_URL"),
		MediaMode:      strings.TrimSpace(os.Getenv("MEDIA_MODE")),
		MediaLocalRoot: strings.TrimSpace(os.Getenv("MEDIA_LOCAL_ROOT")),
	}
	required := map[string]string{"APP_ENV": c.AppEnv, "AUTH_MODE": c.AuthMode, "DATABASE_URL": c.DatabaseURL, "MEDIA_MODE": c.MediaMode}
	if c.AuthMode == AuthModeSupabase {
		required["AUTH_ISSUER"] = c.AuthIssuer
		required["AUTH_AUDIENCE"] = c.AuthAudience
		required["AUTH_JWKS_URL"] = c.AuthJWKSURL
	}
	if c.MediaMode == MediaModeLocal {
		required["MEDIA_LOCAL_ROOT"] = c.MediaLocalRoot
	}
	missing := []string{}
	for key, value := range required {
		if strings.TrimSpace(value) == "" {
			missing = append(missing, key)
		}
	}
	if len(missing) > 0 {
		sort.Strings(missing)
		return Config{}, fmt.Errorf("missing required configuration: %s", strings.Join(missing, ", "))
	}
	if err := c.Validate(); err != nil {
		return Config{}, err
	}
	if c.MediaMode == MediaModeLocal {
		root, err := filepath.Abs(c.MediaLocalRoot)
		if err != nil {
			return Config{}, errors.New("MEDIA_LOCAL_ROOT is not a usable path")
		}
		c.MediaLocalRoot = root
	}
	return c, nil
}

// Validate enforces the environment/auth-mode/media-mode rules. It is
// exported so every component built from a Config can re-assert them, not
// only Load. An empty MediaMode is accepted here and means "no media store"
// (the same as disabled), so a hand-built Config can never enable local
// media implicitly; Load still requires MEDIA_MODE to be set explicitly.
func (c Config) Validate() error {
	switch c.AppEnv {
	case EnvDevelopment, EnvTest, EnvProduction:
	default:
		return fmt.Errorf("APP_ENV must be one of %s, %s, %s", EnvDevelopment, EnvTest, EnvProduction)
	}
	switch c.AuthMode {
	case AuthModeLocal:
		if !LocalAuthPermitted(c.AppEnv) {
			return ErrLocalAuthForbidden
		}
	case AuthModeSupabase:
		if strings.TrimSpace(c.AuthIssuer) == "" || strings.TrimSpace(c.AuthAudience) == "" || strings.TrimSpace(c.AuthJWKSURL) == "" {
			return errors.New("AUTH_MODE=supabase requires AUTH_ISSUER, AUTH_AUDIENCE, and AUTH_JWKS_URL")
		}
	default:
		return fmt.Errorf("AUTH_MODE must be one of %s, %s", AuthModeLocal, AuthModeSupabase)
	}
	switch c.MediaMode {
	case "", MediaModeDisabled:
	case MediaModeLocal:
		if !LocalMediaPermitted(c.AppEnv) {
			return ErrLocalMediaForbidden
		}
		if strings.TrimSpace(c.MediaLocalRoot) == "" {
			return errors.New("MEDIA_MODE=local requires MEDIA_LOCAL_ROOT")
		}
	default:
		return fmt.Errorf("MEDIA_MODE must be one of %s, %s", MediaModeLocal, MediaModeDisabled)
	}
	if strings.TrimSpace(c.HTTPPort) == "" {
		return errors.New("HTTP_PORT cannot be empty")
	}
	return nil
}

// LocalAuthPermitted is an allowlist: only these environments may run local
// development authentication. Every other value is refused.
func LocalAuthPermitted(appEnv string) bool {
	return appEnv == EnvDevelopment || appEnv == EnvTest
}

// LocalMediaPermitted is the equivalent allowlist for local filesystem media.
func LocalMediaPermitted(appEnv string) bool {
	return appEnv == EnvDevelopment || appEnv == EnvTest
}

func get(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
