import React, { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useState } from 'react';
import type { SupabaseClient } from '@supabase/supabase-js';
import { createApiClient } from '../api/client';
import { createEncounterClient, type EncounterClient } from '../api/encounters';
import { pendingCapture } from '../capture/pending-capture';
import { readPublicEnv, resolveAuthConfig, type AuthConfig, type AuthMode } from '../config/auth-mode';
import { createAuthController, type AuthController, type ProfileInput } from './controller';
import { createLocalDevAuthProvider } from './local-dev-adapter';
import { secureSessionStorage } from './secure-storage';
import {
  createSupabaseAuthProvider,
  createSupabaseClient,
  manageSupabaseAutoRefresh,
} from './supabase-adapter';
import { initialAuthState, reduceAuth, type AuthState } from './state';

const CONFIG_ERROR = 'App configuration is incomplete';

type ContextValue = {
  state: AuthState;
  error: string | null;
  /** The resolved auth mode, or null when configuration is invalid. */
  authMode: AuthMode | null;
  /** Encounter/media API for the signed-in user, or null when configuration is invalid. */
  encounters: EncounterClient | null;
  requestOtp(email: string): Promise<void>;
  verifyOtp(email: string, otp: string): Promise<void>;
  signInDevelopmentUser(): Promise<void>;
  updateProfile(input: ProfileInput): Promise<void>;
  signOut(): Promise<void>;
};

type AuthServices = { controller: AuthController; supabase: SupabaseClient | null; encounters: EncounterClient };

const AuthContext = createContext<ContextValue | null>(null);

// Exactly one adapter is constructed for a resolved configuration. Local mode
// never touches Supabase; Supabase mode never constructs the local adapter.
function createAuthServices(config: AuthConfig): AuthServices {
  const api = createApiClient(config.apiUrl);
  if (config.mode === 'local') {
    const controller = createAuthController(createLocalDevAuthProvider(secureSessionStorage), api);
    return { controller, supabase: null, encounters: createEncounterClient(config.apiUrl, () => controller.accessToken()) };
  }
  const client = createSupabaseClient(config.supabaseUrl, config.supabasePublishableKey);
  const controller = createAuthController(createSupabaseAuthProvider(client), api);
  return { controller, supabase: client, encounters: createEncounterClient(config.apiUrl, () => controller.accessToken()) };
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(reduceAuth, initialAuthState);
  const [error, setError] = useState<string | null>(null);

  // Built once so a misconfigured build degrades to GUEST instead of crashing.
  const authServices = useMemo<AuthServices | null>(() => {
    try {
      return createAuthServices(resolveAuthConfig(readPublicEnv(), __DEV__));
    } catch {
      return null;
    }
  }, []);
  const controller = authServices?.controller ?? null;

  useEffect(() => {
    if (!authServices?.supabase) return;
    return manageSupabaseAutoRefresh(authServices.supabase);
  }, [authServices]);

  useEffect(() => {
    let active = true;
    (async () => {
      if (!controller) {
        if (active) {
          setError(CONFIG_ERROR);
          dispatch({ type: 'SESSION_RESOLVED' });
        }
        return;
      }
      try {
        const user = await controller.restore();
        if (!active) return;
        if (user) dispatch({ type: 'BOOTSTRAP_SUCCEEDED', user });
        else dispatch({ type: 'SESSION_RESOLVED' });
      } catch {
        if (!active) return;
        setError('Unable to restore session');
        dispatch({ type: 'SESSION_RESOLVED' });
      }
    })();
    return () => {
      active = false;
    };
  }, [controller]);

  const run = useCallback(async (action: () => Promise<void>) => {
    setError(null);
    try {
      await action();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Request failed');
      throw cause;
    }
  }, []);

  const value = useMemo<ContextValue>(
    () => ({
      state,
      error,
      authMode: controller?.mode ?? null,
      encounters: authServices?.encounters ?? null,
      requestOtp: (email) =>
        run(async () => {
          if (!controller) throw new Error(CONFIG_ERROR);
          await controller.requestOtp(email);
        }),
      verifyOtp: (email, otp) =>
        run(async () => {
          if (!controller) throw new Error(CONFIG_ERROR);
          const user = await controller.verifyOtp(email, otp);
          dispatch({ type: 'BOOTSTRAP_SUCCEEDED', user });
        }),
      signInDevelopmentUser: () =>
        run(async () => {
          if (!controller) throw new Error(CONFIG_ERROR);
          const user = await controller.signInDevelopmentUser();
          dispatch({ type: 'BOOTSTRAP_SUCCEEDED', user });
        }),
      updateProfile: (input) =>
        run(async () => {
          if (!controller) throw new Error(CONFIG_ERROR);
          const user = await controller.updateProfile(input);
          dispatch({ type: 'PROFILE_UPDATED', user });
        }),
      signOut: () =>
        run(async () => {
          if (controller) await controller.signOut();
          // A capture belongs to whoever took it; do not carry it into the next session.
          pendingCapture.discard();
          dispatch({ type: 'SIGNED_OUT' });
        }),
    }),
    [authServices, controller, error, run, state],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): ContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside AuthProvider');
  return value;
}
