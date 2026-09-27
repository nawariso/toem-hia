import type { User } from './state';

export type ProfileInput = { username?: string; displayName?: string; locale?: string };
export interface AuthProvider {
  /** Which adapter this is. Screens use it only to choose the login UI. */
  readonly kind: 'local' | 'supabase';
  getAccessToken(): Promise<string | null>;
  requestEmailOtp(email: string): Promise<void>;
  verifyEmailOtp(email: string, otp: string): Promise<string>;
  /** Local development adapter only; returns a credential for the API to verify. */
  signInDevelopmentUser?(): Promise<string>;
  signOut(): Promise<void>;
}
export interface ApiClient {
  bootstrap(token: string): Promise<User>;
  getMe(token: string): Promise<User>;
  updateMe(token: string, patch: ProfileInput): Promise<User>;
}
export function createAuthController(auth: AuthProvider, api: ApiClient) {
  return {
    mode: auth.kind,
    requestOtp: (email: string) => auth.requestEmailOtp(email.trim().toLowerCase()),
    async verifyOtp(email: string, otp: string) { const token = await auth.verifyEmailOtp(email.trim().toLowerCase(), otp.trim()); return api.bootstrap(token); },
    async signInDevelopmentUser() {
      if (auth.kind !== 'local' || !auth.signInDevelopmentUser) throw new Error('Development sign-in is not available');
      const token = await auth.signInDevelopmentUser();
      try {
        return await api.bootstrap(token);
      } catch (cause) {
        // Do not leave a half-established local session behind a failed bootstrap.
        await auth.signOut();
        throw cause;
      }
    },
    /** The current API credential, or null for a guest. Never persisted by callers. */
    accessToken: () => auth.getAccessToken(),
    async restore() { const token = await auth.getAccessToken(); return token ? api.bootstrap(token) : null; },
    async updateProfile(patch: ProfileInput) { const token = await auth.getAccessToken(); if (!token) throw new Error('Authentication is required'); return api.updateMe(token, patch); },
    signOut: () => auth.signOut(),
  };
}
export type AuthController = ReturnType<typeof createAuthController>;
