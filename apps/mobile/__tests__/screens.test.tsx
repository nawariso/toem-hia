/* eslint-disable import/first -- jest.mock factories must be declared before the modules they mock are imported. */
import React from 'react';
import { render, waitFor, fireEvent, type RenderResult } from '@testing-library/react-native';

// ES imports are hoisted above const initializers, so a jest.mock factory must
// create its own mocks; the test reads them back from the mocked module.
jest.mock('expo-router', () => ({
  router: { replace: jest.fn(), push: jest.fn(), dismissTo: jest.fn() },
  Stack: Object.assign(({ children }: { children?: React.ReactNode }) => children ?? null, {
    Screen: () => null,
  }),
}));

jest.mock('expo-file-system', () => jest.requireActual('./support/fake-file-system'));

jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn().mockResolvedValue(null),
  setItemAsync: jest.fn().mockResolvedValue(undefined),
  deleteItemAsync: jest.fn().mockResolvedValue(undefined),
}));

jest.mock('../src/auth/controller', () => {
  const controller = {
    mode: 'supabase',
    restore: jest.fn(),
    requestOtp: jest.fn(),
    verifyOtp: jest.fn(),
    signInDevelopmentUser: jest.fn(),
    updateProfile: jest.fn(),
    signOut: jest.fn(),
  };
  // The controller reports the mode of whichever real adapter AuthContext chose.
  const createAuthController = jest.fn((auth: { kind: string }) => {
    controller.mode = auth.kind;
    return controller;
  });
  return { createAuthController, __controller: controller };
});
jest.mock('../src/auth/supabase-adapter', () => ({
  createSupabaseClient: jest.fn(() => ({})),
  createSupabaseAuthProvider: () => ({ kind: 'supabase' }),
  manageSupabaseAutoRefresh: () => jest.fn(),
}));
jest.mock('../src/api/client', () => ({ createApiClient: () => ({}) }));

import { router } from 'expo-router';
import { AuthProvider } from '../src/auth/AuthContext';
import Splash from '../app/index';
import Home from '../app/home';
import Passport from '../app/passport';
import PassportSetup from '../app/passport-setup';
import Profile from '../app/profile';

const replace = router.replace as unknown as jest.Mock;
const push = router.push as unknown as jest.Mock;
type ControllerMock = Record<
  'restore' | 'requestOtp' | 'verifyOtp' | 'signInDevelopmentUser' | 'updateProfile' | 'signOut',
  jest.Mock
> & { mode: string };
const controllerModule = jest.requireMock('../src/auth/controller') as {
  __controller: ControllerMock;
  createAuthController: jest.Mock;
};
const controller = controllerModule.__controller;
const createSupabaseClient = (jest.requireMock('../src/auth/supabase-adapter') as { createSupabaseClient: jest.Mock })
  .createSupabaseClient;

const PUBLIC_ENV_KEYS = [
  'EXPO_PUBLIC_APP_ENV',
  'EXPO_PUBLIC_AUTH_MODE',
  'EXPO_PUBLIC_API_URL',
  'EXPO_PUBLIC_SUPABASE_URL',
  'EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY',
] as const;

function useEnv(values: Partial<Record<(typeof PUBLIC_ENV_KEYS)[number], string>>) {
  for (const key of PUBLIC_ENV_KEYS) delete process.env[key];
  Object.assign(process.env, values);
}

const SUPABASE_ENV = {
  EXPO_PUBLIC_APP_ENV: 'production',
  EXPO_PUBLIC_AUTH_MODE: 'supabase',
  EXPO_PUBLIC_API_URL: 'https://api.example',
  EXPO_PUBLIC_SUPABASE_URL: 'https://project.supabase.co',
  EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY: 'sb_publishable_x',
};
const LOCAL_ENV = {
  EXPO_PUBLIC_APP_ENV: 'development',
  EXPO_PUBLIC_AUTH_MODE: 'local',
  EXPO_PUBLIC_API_URL: 'http://192.168.1.20:8080',
};
const DEV_LOGIN_TEXT = /Continue as Dev User|Local Login|Developer Authentication|LOCAL DEVELOPMENT MODE/i;
const originalDev = (globalThis as { __DEV__?: boolean }).__DEV__;

const incomplete = {
  id: 'u1',
  username: null,
  displayName: null,
  avatarUrl: null,
  locale: 'th',
  roles: ['USER'],
  profileComplete: false,
};
const complete = { ...incomplete, username: 'mickey', displayName: 'Mickey', profileComplete: true };

// render and fireEvent are async and already act-wrapped in RNTL 14; wrapping
// them in another act(...) creates overlapping act scopes and drops the event.
function renderScreen(Screen: React.ComponentType): Promise<RenderResult> {
  return render(
    <AuthProvider>
      <Screen />
    </AuthProvider>,
  );
}

async function renderSettled(Screen: React.ComponentType): Promise<RenderResult> {
  const view = await renderScreen(Screen);
  await waitFor(() => expect(controller.restore).toHaveBeenCalled());
  return view;
}

beforeEach(() => {
  jest.clearAllMocks();
  (globalThis as { __DEV__?: boolean }).__DEV__ = originalDev;
  useEnv(SUPABASE_ENV);
  controller.restore.mockResolvedValue(null);
  controller.requestOtp.mockResolvedValue(undefined);
  controller.verifyOtp.mockResolvedValue(incomplete);
  controller.signInDevelopmentUser.mockResolvedValue(incomplete);
  controller.updateProfile.mockResolvedValue(complete);
  controller.signOut.mockResolvedValue(undefined);
});

afterAll(() => {
  (globalThis as { __DEV__?: boolean }).__DEV__ = originalDev;
  useEnv({});
});

describe('guest state', () => {
  it('sends a launching app with no session from Splash to Home', async () => {
    const view = await renderScreen(Splash);
    expect(view.getByText('TOEM HIA')).toBeTruthy();
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/home'));
  });

  it('shows the real Scan a Hia entry and passport wording, never "Register Account"', async () => {
    const view = await renderSettled(Home);
    expect(view.getByText('Scan a Hia')).toBeTruthy();
    expect(view.queryByText('COMING SOON')).toBeNull();
    expect(view.getByText('Create Your Hia Passport')).toBeTruthy();
    expect(view.queryByText(/Register Account/i)).toBeNull();
  });

  it('navigates a guest from Home to the passport screen', async () => {
    const view = await renderSettled(Home);
    await fireEvent.press(view.getByText('Create Your Hia Passport'));
    expect(push).toHaveBeenCalledWith('/passport');
  });
});

describe('login navigation', () => {
  it('requests an OTP, verifies it, and routes an incomplete profile to Passport Setup', async () => {
    const view = await renderSettled(Passport);

    await fireEvent.changeText(view.getByPlaceholderText('you@example.com'), 'person@example.com');
    await fireEvent.press(view.getByText('Email me a code'));
    expect(controller.requestOtp).toHaveBeenCalledWith('person@example.com');

    await fireEvent.changeText(view.getByPlaceholderText('6-digit code'), '123456');
    await fireEvent.press(view.getByText('Verify code'));
    expect(controller.verifyOtp).toHaveBeenCalledWith('person@example.com', '123456');
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/passport-setup'));
  });

  it('surfaces a provider failure instead of navigating', async () => {
    controller.requestOtp.mockRejectedValue(new Error('Email rate limit exceeded'));
    const view = await renderSettled(Passport);

    await fireEvent.changeText(view.getByPlaceholderText('you@example.com'), 'person@example.com');
    await fireEvent.press(view.getByText('Email me a code'));

    await waitFor(() => expect(view.getByText('Email rate limit exceeded')).toBeTruthy());
    expect(replace).not.toHaveBeenCalledWith('/passport-setup');
  });
});

describe('authenticated states', () => {
  it('routes a restored complete profile to Home', async () => {
    controller.restore.mockResolvedValue(complete);
    await renderScreen(Splash);
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/home'));
  });

  it('routes a restored incomplete profile to Passport Setup', async () => {
    controller.restore.mockResolvedValue(incomplete);
    await renderScreen(Splash);
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/passport-setup'));
  });

  it('offers Hia Passport access on Home once authenticated', async () => {
    controller.restore.mockResolvedValue(complete);
    const view = await renderSettled(Home);
    await waitFor(() => expect(view.getByText('View Hia Passport')).toBeTruthy());
    await fireEvent.press(view.getByText('View Hia Passport'));
    expect(push).toHaveBeenCalledWith('/profile');
  });

  it('completes passport setup and moves the user to Profile', async () => {
    controller.restore.mockResolvedValue(incomplete);
    const view = await renderSettled(PassportSetup);

    await fireEvent.changeText(view.getByPlaceholderText('mickey'), 'mickey');
    await fireEvent.changeText(view.getByPlaceholderText('Mickey'), 'Mickey');
    await fireEvent.press(view.getByText('Create Hia Passport'));

    expect(controller.updateProfile).toHaveBeenCalledWith({ username: 'mickey', displayName: 'Mickey', locale: 'th' });
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/profile'));
  });

  it('shows a taken username instead of navigating away', async () => {
    controller.restore.mockResolvedValue(incomplete);
    controller.updateProfile.mockRejectedValue(new Error('Username is already taken'));
    const view = await renderSettled(PassportSetup);

    await fireEvent.changeText(view.getByPlaceholderText('mickey'), 'mickey');
    await fireEvent.changeText(view.getByPlaceholderText('Mickey'), 'Mickey');
    await fireEvent.press(view.getByText('Create Hia Passport'));

    await waitFor(() => expect(view.getByText('Username is already taken')).toBeTruthy());
    expect(replace).not.toHaveBeenCalledWith('/profile');
  });

  it('shows the internal user and role on Profile', async () => {
    controller.restore.mockResolvedValue(complete);
    const view = await renderSettled(Profile);
    await waitFor(() => expect(view.getByText('Mickey')).toBeTruthy());
    expect(view.getByText('@mickey')).toBeTruthy();
    expect(view.getByText('USER')).toBeTruthy();
  });
});

describe('logout navigation', () => {
  it('signs out through the provider and returns to guest Home', async () => {
    controller.restore.mockResolvedValue(complete);
    const view = await renderSettled(Profile);
    await waitFor(() => expect(view.getByText('Mickey')).toBeTruthy());

    await fireEvent.press(view.getByText('Log out'));

    expect(controller.signOut).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/home'));
  });
});

describe('local development auth mode (Requirement 001-B)', () => {
  beforeEach(() => useEnv(LOCAL_ENV));

  it('offers Continue as Dev User, labels the mode, and hides the email OTP form', async () => {
    const view = await renderSettled(Passport);
    expect(view.getByText('Continue as Dev User')).toBeTruthy();
    expect(view.getByText('LOCAL DEVELOPMENT MODE')).toBeTruthy();
    expect(view.queryByPlaceholderText('you@example.com')).toBeNull();
    expect(view.queryByText('Email me a code')).toBeNull();
  });

  it('never constructs a Supabase client in local mode', async () => {
    await renderSettled(Passport);
    expect(createSupabaseClient).not.toHaveBeenCalled();
    expect(controllerModule.createAuthController).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'local' }),
      expect.anything(),
    );
  });

  it('dev login bootstraps through the API and reaches the authenticated state', async () => {
    const view = await renderSettled(Passport);
    await fireEvent.press(view.getByText('Continue as Dev User'));
    expect(controller.signInDevelopmentUser).toHaveBeenCalledTimes(1);
    expect(controller.requestOtp).not.toHaveBeenCalled();
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/passport-setup'));
  });

  it('surfaces an API failure instead of navigating', async () => {
    controller.signInDevelopmentUser.mockRejectedValue(new Error('Network request failed'));
    const view = await renderSettled(Passport);
    await fireEvent.press(view.getByText('Continue as Dev User'));
    await waitFor(() => expect(view.getByText('Network request failed')).toBeTruthy());
    expect(replace).not.toHaveBeenCalled();
  });

  it('logout from a local session returns to guest Home', async () => {
    controller.restore.mockResolvedValue(complete);
    const view = await renderSettled(Profile);
    await waitFor(() => expect(view.getByText('Mickey')).toBeTruthy());
    await fireEvent.press(view.getByText('Log out'));
    expect(controller.signOut).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/home'));
    // The local adapter cleared its stored session (see local-dev-adapter.test.ts),
    // so a relaunch restores nothing and lands on guest Home.
    controller.restore.mockResolvedValue(null);
    const home = await renderSettled(Home);
    expect(home.getByText('Create Your Hia Passport')).toBeTruthy();
  });
});

describe('production UX isolation (Requirement 001-B §13)', () => {
  const screens: [string, React.ComponentType][] = [
    ['Splash', Splash],
    ['Home', Home],
    ['Passport', Passport],
    ['PassportSetup', PassportSetup],
    ['Profile', Profile],
  ];

  it.each(screens)('supabase-mode %s never shows development login', async (_name, Screen) => {
    const view = await renderSettled(Screen);
    expect(view.queryByText(DEV_LOGIN_TEXT)).toBeNull();
  });

  it('supabase mode shows the email OTP form instead', async () => {
    const view = await renderSettled(Passport);
    expect(view.getByText('Email me a code')).toBeTruthy();
    expect(controllerModule.createAuthController).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'supabase' }),
      expect.anything(),
    );
  });

  it('production + local configuration exposes no dev login and fails closed', async () => {
    useEnv({ ...LOCAL_ENV, EXPO_PUBLIC_APP_ENV: 'production' });
    const view = await renderScreen(Passport);
    await waitFor(() => expect(view.getByText('App configuration is incomplete')).toBeTruthy());
    expect(view.queryByText(DEV_LOGIN_TEXT)).toBeNull();
    expect(controllerModule.createAuthController).not.toHaveBeenCalled();
  });

  it('a release bundle (__DEV__=false) refuses local mode even with a development APP_ENV', async () => {
    (globalThis as { __DEV__?: boolean }).__DEV__ = false;
    useEnv(LOCAL_ENV);
    const view = await renderScreen(Passport);
    await waitFor(() => expect(view.getByText('App configuration is incomplete')).toBeTruthy());
    expect(view.queryByText(DEV_LOGIN_TEXT)).toBeNull();
    expect(controllerModule.createAuthController).not.toHaveBeenCalled();
  });
});
