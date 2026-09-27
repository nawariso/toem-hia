import { initialAuthState, reduceAuth, routeAfterSignIn, routeForState } from '../src/auth/state';

describe('authentication state and navigation policy', () => {
  it('starts UNKNOWN then becomes GUEST on first launch without a session', () => {
    expect(initialAuthState.status).toBe('UNKNOWN');
    const state = reduceAuth(initialAuthState, { type: 'SESSION_RESOLVED' });
    expect(state.status).toBe('GUEST');
    expect(routeForState(state)).toBe('/home');
  });

  it('routes an authenticated incomplete profile to passport setup', () => {
    const state = reduceAuth(initialAuthState, { type: 'BOOTSTRAP_SUCCEEDED', user: { id: 'u1', username: null, displayName: null, avatarUrl: null, locale: 'th', roles: ['USER'], profileComplete: false } });
    expect(state.status).toBe('AUTHENTICATED');
    expect(routeForState(state)).toBe('/passport-setup');
  });

  it('routes an authenticated complete profile to home', () => {
    const state = reduceAuth(initialAuthState, { type: 'BOOTSTRAP_SUCCEEDED', user: { id: 'u1', username: 'mickey', displayName: 'Mickey', avatarUrl: null, locale: 'th', roles: ['USER'], profileComplete: true } });
    expect(routeForState(state)).toBe('/home');
  });

  it('logout clears authenticated state and returns home as guest', () => {
    const authenticated = reduceAuth(initialAuthState, { type: 'BOOTSTRAP_SUCCEEDED', user: { id: 'u1', username: 'mickey', displayName: 'Mickey', avatarUrl: null, locale: 'th', roles: ['USER'], profileComplete: true } });
    const state = reduceAuth(authenticated, { type: 'SIGNED_OUT' });
    expect(state.status).toBe('GUEST');
    expect(state.user).toBeNull();
    expect(routeForState(state)).toBe('/home');
  });

  it('a pending capture save returns to Scan only after the profile is complete', () => {
    const user = { id: 'u1', username: null, displayName: null, avatarUrl: null, locale: 'th', roles: ['USER'], profileComplete: false };
    const incomplete = reduceAuth(initialAuthState, { type: 'BOOTSTRAP_SUCCEEDED', user });
    const complete = reduceAuth(initialAuthState, { type: 'BOOTSTRAP_SUCCEEDED', user: { ...user, username: 'mickey', displayName: 'Mickey', profileComplete: true } });
    expect(routeAfterSignIn(incomplete, true)).toBe('/passport-setup');
    expect(routeAfterSignIn(complete, true)).toBe('/scan');
    expect(routeAfterSignIn(complete, false)).toBe('/home');
  });
});
