package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/nawariso/toem-hia/services/api/internal/application"
	"github.com/nawariso/toem-hia/services/api/internal/infrastructure/config"
	"github.com/nawariso/toem-hia/services/api/internal/infrastructure/identity"
	"github.com/nawariso/toem-hia/services/api/internal/infrastructure/media/localmedia"
	persistence "github.com/nawariso/toem-hia/services/api/internal/infrastructure/persistence/postgres"
	httptransport "github.com/nawariso/toem-hia/services/api/internal/transport/http"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	slog.SetDefault(logger)
	cfg, err := config.Load()
	if err != nil {
		logger.Error("configuration_invalid", "error", err)
		os.Exit(1)
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, cfg.DatabaseURL)
	if err != nil {
		logger.Error("database_configuration_invalid", "error", err)
		os.Exit(1)
	}
	defer pool.Close()
	if err = pool.Ping(ctx); err != nil {
		logger.Error("database_unavailable", "error", err)
		os.Exit(1)
	}
	verifier, err := identity.FromConfig(cfg)
	if err != nil {
		logger.Error("identity_verifier_invalid", "error", err)
		os.Exit(1)
	}
	if cfg.AuthMode == config.AuthModeLocal {
		logger.Warn("local_development_auth_enabled",
			"auth_mode", cfg.AuthMode, "environment", cfg.AppEnv,
			"notice", "LOCAL AUTH IS DEVELOPMENT ONLY AND MUST NEVER BE ENABLED IN PRODUCTION")
	}
	repo := persistence.NewRepository(pool)
	users := application.NewUserService(repo)
	parkRepo := persistence.NewParkRepository(pool)
	encounterRepo := persistence.NewEncounterRepository(pool)
	wildlife := httptransport.WithWildlife(
		application.NewParkService(parkRepo),
		application.NewHiaService(persistence.NewHiaRepository(pool)),
		application.NewEncounterService(users, parkRepo, encounterRepo),
	)
	options := []httptransport.Option{httptransport.WithLogger(logger), wildlife}
	switch cfg.MediaMode {
	case config.MediaModeLocal:
		store, storeErr := localmedia.New(cfg.MediaLocalRoot)
		if storeErr != nil {
			// The error can name the root; it is operator configuration, not
			// user data, but it is still not echoed to clients.
			logger.Error("media_store_unavailable", "media_mode", cfg.MediaMode)
			os.Exit(1)
		}
		options = append(options, httptransport.WithMedia(
			application.NewMediaService(users, encounterRepo, persistence.NewMediaRepository(pool), store)))
		logger.Warn("local_media_storage_enabled", "media_mode", cfg.MediaMode, "environment", cfg.AppEnv,
			"notice", "LOCAL MEDIA STORAGE IS DEVELOPMENT ONLY AND MUST NEVER BE ENABLED IN PRODUCTION")
	default:
		logger.Info("media_storage_disabled", "media_mode", cfg.MediaMode)
	}
	handler := httptransport.NewServer(users, verifier, repo, options...).Handler()
	server := &http.Server{Addr: ":" + cfg.HTTPPort, Handler: handler, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 15 * time.Second, IdleTimeout: 60 * time.Second}
	go func() {
		logger.Info("api_started", "port", cfg.HTTPPort, "environment", cfg.AppEnv, "auth_mode", cfg.AuthMode, "media_mode", cfg.MediaMode)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Error("api_stopped", "error", err)
			os.Exit(1)
		}
	}()
	stopCtx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	<-stopCtx.Done()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := server.Shutdown(shutdownCtx); err != nil {
		logger.Error("shutdown_failed", "error", err)
	}
}
