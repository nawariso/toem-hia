export type User = {
  id: string;
  username: string | null;
  displayName: string | null;
  avatarUrl: string | null;
  locale: string;
  roles: string[];
  profileComplete: boolean;
};

export type AuthState = { status: 'UNKNOWN' | 'GUEST' | 'AUTHENTICATED'; user: User | null };
export type AuthAction =
  | { type: 'SESSION_RESOLVED' }
  | { type: 'BOOTSTRAP_SUCCEEDED'; user: User }
  | { type: 'PROFILE_UPDATED'; user: User }
  | { type: 'SIGNED_OUT' };

export const initialAuthState: AuthState = { status: 'UNKNOWN', user: null };

export function reduceAuth(_state: AuthState, action: AuthAction): AuthState {
  switch (action.type) {
    // A resolved session without an internal user is a guest; an authenticated
    // user only ever arrives through BOOTSTRAP_SUCCEEDED, so there is no state
    // in which the app can remain stuck on UNKNOWN after resolution.
    case 'SESSION_RESOLVED': return { status: 'GUEST', user: null };
    case 'BOOTSTRAP_SUCCEEDED':
    case 'PROFILE_UPDATED': return { status: 'AUTHENTICATED', user: action.user };
    case 'SIGNED_OUT': return { status: 'GUEST', user: null };
  }
}

export function routeForState(state: AuthState): '/home' | '/passport-setup' {
  return state.status === 'AUTHENTICATED' && state.user && !state.user.profileComplete ? '/passport-setup' : '/home';
}

/**
 * Where a sign-in lands. Passport Setup still comes first for an incomplete
 * profile; after that a guest who pressed Save on a capture returns to Scan
 * to finish that save instead of being sent Home.
 */
export function routeAfterSignIn(state: AuthState, resumeCapture: boolean): '/home' | '/passport-setup' | '/scan' {
  const route = routeForState(state);
  return route === '/home' && resumeCapture ? '/scan' : route;
}
