package main

import (
	"os"
	"os/exec"
	"strings"
	"testing"
)

// TestMain lets the test binary act as the real API process so startup
// behaviour is asserted end to end, including the process exit code.
func TestMain(m *testing.M) {
	if os.Getenv("TOEM_RUN_API_MAIN") == "1" {
		main()
		os.Exit(0)
	}
	os.Exit(m.Run())
}

func runAPI(t *testing.T, env ...string) (int, string) {
	t.Helper()
	cmd := exec.Command(os.Args[0], "-test.run=^$")
	base := []string{"TOEM_RUN_API_MAIN=1", "SYSTEMROOT=" + os.Getenv("SYSTEMROOT"), "PATH=" + os.Getenv("PATH")}
	cmd.Env = append(base, env...)
	out, err := cmd.CombinedOutput()
	if err == nil {
		return 0, string(out)
	}
	exitErr, ok := err.(*exec.ExitError)
	if !ok {
		t.Fatalf("could not start API process: %v", err)
	}
	return exitErr.ExitCode(), string(out)
}

// Requirement 001-B §7/§40: APP_ENV=production + AUTH_MODE=local is a fatal
// startup error. The database URL points nowhere, proving the process refuses
// before connecting to anything or serving a request.
func TestProductionWithLocalAuthTerminatesStartup(t *testing.T) {
	code, out := runAPI(t,
		"APP_ENV=production", "AUTH_MODE=local", "MEDIA_MODE=disabled",
		"DATABASE_URL=postgres://unused:unused@127.0.0.1:1/unused?sslmode=disable",
		"AUTH_ISSUER=https://issuer.example/auth/v1", "AUTH_AUDIENCE=authenticated", "AUTH_JWKS_URL=https://issuer.example/jwks",
	)
	if code == 0 {
		t.Fatalf("production + local must not start; output=%s", out)
	}
	if !strings.Contains(out, "configuration_invalid") || !strings.Contains(out, "development-only") {
		t.Fatalf("expected a configuration error naming the local-auth guard, got %s", out)
	}
	if strings.Contains(out, "api_started") || strings.Contains(out, "database_unavailable") {
		t.Fatalf("process progressed past configuration validation: %s", out)
	}
}

func TestUnknownOrMissingAuthModeTerminatesStartup(t *testing.T) {
	for _, mode := range []string{"AUTH_MODE=", "AUTH_MODE=disabled"} {
		code, out := runAPI(t, "APP_ENV=development", mode, "MEDIA_MODE=disabled", "DATABASE_URL=postgres://unused@127.0.0.1:1/x")
		if code == 0 || !strings.Contains(out, "configuration_invalid") {
			t.Fatalf("%s must fail startup: code=%d out=%s", mode, code, out)
		}
	}
}

// Requirement 003: production must never keep private photos on an arbitrary
// local disk. The process refuses before connecting to the database.
func TestProductionWithLocalMediaTerminatesStartup(t *testing.T) {
	code, out := runAPI(t,
		"APP_ENV=production", "AUTH_MODE=supabase", "MEDIA_MODE=local", "MEDIA_LOCAL_ROOT="+t.TempDir(),
		"DATABASE_URL=postgres://unused@127.0.0.1:1/unused?sslmode=disable",
		"AUTH_ISSUER=https://issuer.example/auth/v1", "AUTH_AUDIENCE=authenticated", "AUTH_JWKS_URL=https://issuer.example/jwks",
	)
	if code == 0 {
		t.Fatalf("production + local media must not start; output=%s", out)
	}
	if !strings.Contains(out, "configuration_invalid") || !strings.Contains(out, "MEDIA_MODE=local is development-only") {
		t.Fatalf("expected a configuration error naming the local-media guard, got %s", out)
	}
	if strings.Contains(out, "api_started") || strings.Contains(out, "database_unavailable") {
		t.Fatalf("process progressed past configuration validation: %s", out)
	}
}

func TestMissingMediaModeTerminatesStartup(t *testing.T) {
	code, out := runAPI(t, "APP_ENV=development", "AUTH_MODE=local", "DATABASE_URL=postgres://unused@127.0.0.1:1/x")
	if code == 0 || !strings.Contains(out, "MEDIA_MODE") {
		t.Fatalf("a missing MEDIA_MODE must fail startup naming it: code=%d out=%s", code, out)
	}
}
