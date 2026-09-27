/* eslint-disable import/first -- jest.mock factories must be declared before the modules they mock are imported. */
import React from 'react';
import { act, fireEvent, render, waitFor, type RenderResult } from '@testing-library/react-native';

jest.mock('expo-file-system', () => jest.requireActual('./support/fake-file-system'));

jest.mock('expo-router', () => {
  const focus = { focused: true };
  return {
    __focus: focus,
    router: { replace: jest.fn(), push: jest.fn(), dismissTo: jest.fn() },
    useIsFocused: () => focus.focused,
    // Runs the focus callback once (mounted & focused) and its cleanup on unmount.
    useFocusEffect: (effect: () => undefined | (() => void)) => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { useEffect } = require('react') as typeof import('react');
      useEffect(() => (focus.focused ? effect() : undefined), [effect]);
    },
    Stack: Object.assign(({ children }: { children?: React.ReactNode }) => children ?? null, { Screen: () => null }),
  };
});

jest.mock('expo-camera', () => {
  const ReactActual = jest.requireActual('react') as typeof import('react');
  const camera = {
    permission: null as null | { granted: boolean; canAskAgain: boolean; status: string },
    requestPermission: jest.fn(),
    takePictureAsync: jest.fn(),
    readyHandlers: [] as (() => void)[],
    mounted: 0,
    props: [] as Record<string, unknown>[],
  };
  const CameraView = ReactActual.forwardRef(function CameraView(
    props: { onCameraReady?: () => void; [key: string]: unknown },
    ref: React.Ref<{ takePictureAsync: jest.Mock }>,
  ) {
    ReactActual.useImperativeHandle(ref, () => ({ takePictureAsync: camera.takePictureAsync }));
    ReactActual.useEffect(() => {
      camera.mounted += 1;
      camera.props.push(props);
      if (props.onCameraReady) camera.readyHandlers.push(props.onCameraReady);
      return () => {
        camera.mounted -= 1;
      };
    }, []);
    return null;
  });
  return {
    __camera: camera,
    CameraView,
    useCameraPermissions: () => [camera.permission, camera.requestPermission],
  };
});

jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn().mockResolvedValue(null),
  setItemAsync: jest.fn().mockResolvedValue(undefined),
  deleteItemAsync: jest.fn().mockResolvedValue(undefined),
}));

jest.mock('../src/auth/controller', () => {
  const controller = {
    mode: 'local',
    restore: jest.fn(),
    requestOtp: jest.fn(),
    verifyOtp: jest.fn(),
    signInDevelopmentUser: jest.fn(),
    updateProfile: jest.fn(),
    signOut: jest.fn(),
    accessToken: jest.fn(),
  };
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
jest.mock('../src/api/encounters', () => {
  const client = {
    createDraftEncounter: jest.fn(),
    uploadPhoto: jest.fn(),
    submitEncounter: jest.fn(),
    getEncounter: jest.fn(),
  };
  return { createEncounterClient: jest.fn(() => client), __client: client };
});

import { router } from 'expo-router';
import { fakeFs } from './support/fake-file-system';
import { AuthProvider } from '../src/auth/AuthContext';
import { pendingCapture } from '../src/capture/pending-capture';
import { GUIDANCE, MOTTO } from '../src/capture/guidance';
import Scan from '../app/scan';
import Home from '../app/home';
import Passport from '../app/passport';
import PassportSetup from '../app/passport-setup';
import Profile from '../app/profile';

type CameraState = {
  permission: null | { granted: boolean; canAskAgain: boolean; status: string };
  requestPermission: jest.Mock;
  takePictureAsync: jest.Mock;
  readyHandlers: (() => void)[];
  mounted: number;
  props: Record<string, unknown>[];
};
const camera = (jest.requireMock('expo-camera') as { __camera: CameraState }).__camera;
const focus = (jest.requireMock('expo-router') as { __focus: { focused: boolean } }).__focus;
const controller = (
  jest.requireMock('../src/auth/controller') as {
    __controller: Record<'restore' | 'signInDevelopmentUser' | 'updateProfile' | 'signOut' | 'accessToken', jest.Mock>;
  }
).__controller;
const api = (jest.requireMock('../src/api/encounters') as { __client: Record<string, jest.Mock> }).__client;
const createSupabaseClient = (jest.requireMock('../src/auth/supabase-adapter') as { createSupabaseClient: jest.Mock })
  .createSupabaseClient;
const push = router.push as unknown as jest.Mock;
const replace = router.replace as unknown as jest.Mock;
const dismissTo = router.dismissTo as unknown as jest.Mock;

const LOCAL_ENV = {
  EXPO_PUBLIC_APP_ENV: 'development',
  EXPO_PUBLIC_AUTH_MODE: 'local',
  EXPO_PUBLIC_API_URL: 'http://192.168.1.20:8080',
};
const SUPABASE_ENV = {
  EXPO_PUBLIC_APP_ENV: 'production',
  EXPO_PUBLIC_AUTH_MODE: 'supabase',
  EXPO_PUBLIC_API_URL: 'https://api.example',
  EXPO_PUBLIC_SUPABASE_URL: 'https://project.supabase.co',
  EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY: 'sb_publishable_x',
};
const ENV_KEYS = [...new Set([...Object.keys(LOCAL_ENV), ...Object.keys(SUPABASE_ENV)])];
function useEnv(values: Record<string, string>) {
  for (const key of ENV_KEYS) delete process.env[key];
  Object.assign(process.env, values);
}

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
const GRANTED = { granted: true, canAskAgain: true, status: 'granted' };
const draft = { id: 'enc-1', status: 'DRAFT' };
const submitted = { id: 'enc-1', status: 'SUBMITTED' };

async function renderSettled(Screen: React.ComponentType): Promise<RenderResult> {
  const view = await render(
    <AuthProvider>
      <Screen />
    </AuthProvider>,
  );
  await waitFor(() => expect(controller.restore).toHaveBeenCalled());
  return view;
}

async function cameraReady() {
  await waitFor(() => expect(camera.readyHandlers.length).toBeGreaterThan(0));
  await act(async () => camera.readyHandlers[camera.readyHandlers.length - 1]());
}

async function capture(view: RenderResult) {
  await cameraReady();
  await fireEvent.press(view.getByLabelText('Take photo'));
  await waitFor(() => expect(view.getByTestId('capture-preview')).toBeTruthy());
}

beforeEach(() => {
  jest.clearAllMocks();
  fakeFs.reset();
  pendingCapture.discard();
  useEnv(LOCAL_ENV);
  focus.focused = true;
  camera.permission = GRANTED;
  camera.readyHandlers = [];
  camera.mounted = 0;
  camera.props = [];
  camera.requestPermission.mockResolvedValue(GRANTED);
  camera.takePictureAsync.mockImplementation(() =>
    Promise.resolve({ uri: fakeFs.cameraFile(), width: 640, height: 480, format: 'jpg' }),
  );
  controller.restore.mockResolvedValue(null);
  controller.signInDevelopmentUser.mockResolvedValue(complete);
  controller.updateProfile.mockResolvedValue(complete);
  controller.signOut.mockResolvedValue(undefined);
  controller.accessToken.mockResolvedValue('token');
  api.createDraftEncounter.mockResolvedValue(draft);
  api.uploadPhoto.mockResolvedValue({ id: 'media-1' });
  api.submitEncounter.mockResolvedValue(submitted);
  api.getEncounter.mockResolvedValue(draft);
});

afterAll(() => useEnv({}));

describe('Scan: camera permission', () => {
  it('shows a loading state while permission is unknown and mounts no camera', async () => {
    camera.permission = null;
    const view = await renderSettled(Scan);
    expect(view.getByText('Checking camera permission…')).toBeTruthy();
    expect(camera.mounted).toBe(0);
  });

  it('requests permission from the user with the field guidance visible', async () => {
    camera.permission = { granted: false, canAskAgain: true, status: 'undetermined' };
    const view = await renderSettled(Scan);
    for (const line of GUIDANCE) expect(view.getByText(line)).toBeTruthy();
    expect(view.getByText(MOTTO)).toBeTruthy();
    await fireEvent.press(view.getByText('Allow camera'));
    expect(camera.requestPermission).toHaveBeenCalledTimes(1);
    expect(camera.mounted).toBe(0);
  });

  it('explains a permanently denied permission and offers Settings, not a camera', async () => {
    camera.permission = { granted: false, canAskAgain: false, status: 'denied' };
    const view = await renderSettled(Scan);
    expect(view.getByText('Camera access is turned off for TOEM HIA.')).toBeTruthy();
    expect(view.getByText('Open Settings')).toBeTruthy();
    expect(view.queryByText('Allow camera')).toBeNull();
    expect(camera.mounted).toBe(0);
  });
});

describe('Scan: camera', () => {
  it('mounts one rear, photo-mode camera with guidance', async () => {
    const view = await renderSettled(Scan);
    expect(camera.mounted).toBe(1);
    expect(camera.props[0]).toMatchObject({ facing: 'back', mode: 'picture' });
    for (const line of [...GUIDANCE, MOTTO]) expect(view.getByText(line)).toBeTruthy();
  });

  it('cannot capture before the camera reports ready', async () => {
    const view = await renderSettled(Scan);
    expect(view.getByText('Starting camera…')).toBeTruthy();
    await fireEvent.press(view.getByLabelText('Take photo'));
    expect(camera.takePictureAsync).not.toHaveBeenCalled();
  });

  it('captures after ready, without Base64 or EXIF, and shows the preview with no camera mounted', async () => {
    const view = await renderSettled(Scan);
    await capture(view);
    expect(camera.takePictureAsync).toHaveBeenCalledTimes(1);
    expect(camera.takePictureAsync.mock.calls[0][0]).toMatchObject({ base64: false, exif: false });
    expect(camera.mounted).toBe(0);
    expect(view.getByText('Retake')).toBeTruthy();
    expect(view.getByText('Save Encounter')).toBeTruthy();
    const photo = pendingCapture.get()?.photo;
    expect(photo?.uri).toMatch(/^file:\/\/\/documents\/toem-hia\/pending-capture\//);
    expect(view.getByTestId('capture-preview').props.source).toEqual({ uri: photo?.uri });
  });

  it('shows a capture failure and stays on the camera', async () => {
    camera.takePictureAsync.mockRejectedValueOnce(new Error('hardware'));
    const view = await renderSettled(Scan);
    await cameraReady();
    await fireEvent.press(view.getByLabelText('Take photo'));
    await waitFor(() => expect(view.getByText('Capture failed. Try again.')).toBeTruthy());
    expect(pendingCapture.get()).toBeNull();
    expect(camera.mounted).toBe(1);
  });

  it('shows capture failure when the pending move fails without deleting the camera source', async () => {
    const { moveFailure } = jest.requireMock('expo-file-system') as { moveFailure: { next: Error | null } };
    moveFailure.next = new Error('disk full');
    const cameraUri = fakeFs.cameraFile('failed-move.jpg');
    camera.takePictureAsync.mockResolvedValueOnce({ uri: cameraUri, format: 'jpg' });
    const view = await renderSettled(Scan);
    await cameraReady();
    await fireEvent.press(view.getByLabelText('Take photo'));
    await waitFor(() => expect(view.getByText('Capture failed. Try again.')).toBeTruthy());
    expect(pendingCapture.get()).toBeNull();
    expect(fakeFs.all()).toEqual([cameraUri]);
  });

  it('does not mount the camera while the screen is not focused', async () => {
    focus.focused = false;
    await renderSettled(Scan);
    expect(camera.mounted).toBe(0);
  });

  it('Retake removes the pending file and returns to a camera that must become ready again', async () => {
    const view = await renderSettled(Scan);
    await capture(view);
    const first = pendingCapture.get()!.photo!.uri;
    await fireEvent.press(view.getByText('Retake'));
    expect(fakeFs.exists(first)).toBe(false);
    expect(pendingCapture.get()).toBeNull();
    expect(camera.mounted).toBe(1);
    expect(view.getByText('Starting camera…')).toBeTruthy();
  });

  it('Cancel removes the pending file and leaves Scan', async () => {
    const view = await renderSettled(Scan);
    await capture(view);
    const uri = pendingCapture.get()!.photo!.uri;
    await fireEvent.press(view.getByText('Cancel'));
    expect(fakeFs.exists(uri)).toBe(false);
    expect(replace).toHaveBeenCalledWith('/home');
  });
});

describe('Scan: guest save', () => {
  it('asks a guest to save this Hia to their Passport and keeps the capture', async () => {
    const view = await renderSettled(Scan);
    await capture(view);
    expect(view.getByText('Save this Hia to your Passport')).toBeTruthy();
    await fireEvent.press(view.getByText('Save Encounter'));
    expect(push).toHaveBeenCalledWith('/passport');
    expect(api.createDraftEncounter).not.toHaveBeenCalled();
    expect(pendingCapture.get()).toMatchObject({ saveRequested: true });
    expect(fakeFs.exists(pendingCapture.get()!.photo!.uri)).toBe(true);
  });

  it('local dev login returns to the pending capture instead of Home', async () => {
    const view = await renderSettled(Scan);
    await capture(view);
    await fireEvent.press(view.getByText('Save Encounter'));
    await view.unmount();

    const passport = await renderSettled(Passport);
    await fireEvent.press(passport.getByText('Continue as Dev User'));
    await waitFor(() => expect(dismissTo).toHaveBeenCalledWith('/scan'));
    expect(replace).not.toHaveBeenCalledWith('/home');
    expect(pendingCapture.get()?.photo).not.toBeNull();
  });

  it('Passport Setup sends a user with a pending save back to Scan', async () => {
    const view = await renderSettled(Scan);
    await capture(view);
    await fireEvent.press(view.getByText('Save Encounter'));
    await view.unmount();

    controller.restore.mockResolvedValue(incomplete);
    const setup = await renderSettled(PassportSetup);
    await fireEvent.changeText(setup.getByPlaceholderText('mickey'), 'mickey');
    await fireEvent.changeText(setup.getByPlaceholderText('Mickey'), 'Mickey');
    await fireEvent.press(setup.getByText('Create Hia Passport'));
    await waitFor(() => expect(dismissTo).toHaveBeenCalledWith('/scan'));
    expect(replace).not.toHaveBeenCalledWith('/profile');
  });

  it('resumes the pending save automatically once signed in with a complete profile', async () => {
    const guest = await renderSettled(Scan);
    await capture(guest);
    await fireEvent.press(guest.getByText('Save Encounter'));
    const photoUri = pendingCapture.get()!.photo!.uri;
    await guest.unmount();

    controller.restore.mockResolvedValue(complete);
    const view = await renderSettled(Scan);
    await waitFor(() => expect(view.getByText('Encounter saved')).toBeTruthy());
    expect(view.getByText('Your encounter is saved in your Hia Passport. Only you can see the photo.')).toBeTruthy();
    expect(api.createDraftEncounter).toHaveBeenCalledTimes(1);
    expect(api.uploadPhoto).toHaveBeenCalledTimes(1);
    expect(api.submitEncounter).toHaveBeenCalledTimes(1);
    expect(fakeFs.exists(photoUri)).toBe(false);
    expect(pendingCapture.get()).toBeNull();
  });
});

describe('Scan: authenticated save', () => {
  beforeEach(() => controller.restore.mockResolvedValue(complete));

  it('creates exactly one Encounter even when Save is tapped repeatedly', async () => {
    let finishCreate: (value: unknown) => void = () => undefined;
    api.createDraftEncounter.mockReturnValue(new Promise((resolve) => (finishCreate = resolve)));
    const view = await renderSettled(Scan);
    await capture(view);
    const save = view.getByText('Save Encounter');
    await fireEvent.press(save);
    await fireEvent.press(save);
    expect(view.getByText('Saving encounter…')).toBeTruthy();
    expect(view.queryByText('Save Encounter')).toBeNull();
    finishCreate(draft);
    await waitFor(() => expect(view.getByText('Encounter saved')).toBeTruthy());
    expect(api.createDraftEncounter).toHaveBeenCalledTimes(1);
    expect(api.uploadPhoto).toHaveBeenCalledTimes(1);
  });

  it('upload failure keeps the photo and Retry reuses the same Encounter', async () => {
    api.uploadPhoto.mockRejectedValueOnce(new Error('offline'));
    const view = await renderSettled(Scan);
    await capture(view);
    const uri = pendingCapture.get()!.photo!.uri;
    await fireEvent.press(view.getByText('Save Encounter'));
    await waitFor(() => expect(view.getByText('Upload failed. Your photo is kept on this device.')).toBeTruthy());
    expect(fakeFs.exists(uri)).toBe(true);
    expect(view.getByTestId('capture-preview')).toBeTruthy();

    await fireEvent.press(view.getByText('Retry'));
    await waitFor(() => expect(view.getByText('Encounter saved')).toBeTruthy());
    expect(api.createDraftEncounter).toHaveBeenCalledTimes(1);
    expect(api.uploadPhoto).toHaveBeenCalledTimes(2);
    expect(fakeFs.exists(uri)).toBe(false);
  });

  it('submit failure is not reported as saved, and Retry does not re-upload', async () => {
    api.submitEncounter.mockRejectedValueOnce(new Error('offline'));
    const view = await renderSettled(Scan);
    await capture(view);
    await fireEvent.press(view.getByText('Save Encounter'));
    await waitFor(() =>
      expect(view.getByText('Your photo is uploaded, but the encounter is not submitted yet.')).toBeTruthy(),
    );
    expect(view.queryByText('Encounter saved')).toBeNull();
    expect(view.queryByText('Retake')).toBeNull();

    await fireEvent.press(view.getByText('Retry'));
    await waitFor(() => expect(view.getByText('Encounter saved')).toBeTruthy());
    expect(api.uploadPhoto).toHaveBeenCalledTimes(1);
    expect(api.submitEncounter).toHaveBeenCalledTimes(2);
    expect(api.createDraftEncounter).toHaveBeenCalledTimes(1);
  });

  it('sends only capturedAt when creating the Encounter', async () => {
    const view = await renderSettled(Scan);
    await capture(view);
    await fireEvent.press(view.getByText('Save Encounter'));
    await waitFor(() => expect(view.getByText('Encounter saved')).toBeTruthy());
    expect(api.createDraftEncounter).toHaveBeenCalledWith(expect.stringMatching(/^\d{4}-\d{2}-\d{2}T/));
    expect(api.createDraftEncounter.mock.calls[0]).toHaveLength(1);
  });
});

describe('Home, logout, and mode isolation', () => {
  it('Home Scan a Hia opens the Scan screen', async () => {
    const view = await renderSettled(Home);
    expect(view.queryByText('COMING SOON')).toBeNull();
    await fireEvent.press(view.getByText('Scan a Hia'));
    expect(push).toHaveBeenCalledWith('/scan');
  });

  it('logout discards a pending capture and returns to guest Home', async () => {
    controller.restore.mockResolvedValue(complete);
    await pendingCapture.keep(fakeFs.cameraFile(), 'image/jpeg', '2026-09-26T10:00:00.000Z');
    const uri = pendingCapture.get()!.photo!.uri;
    const view = await renderSettled(Profile);
    await fireEvent.press(view.getByText('Log out'));
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/home'));
    expect(controller.signOut).toHaveBeenCalledTimes(1);
    expect(pendingCapture.get()).toBeNull();
    expect(fakeFs.exists(uri)).toBe(false);
  });

  it('local mode never constructs a Supabase client for the capture flow', async () => {
    const view = await renderSettled(Scan);
    await capture(view);
    expect(createSupabaseClient).not.toHaveBeenCalled();
  });

  it('Supabase mode Passport shows no dev login and still returns to a pending capture', async () => {
    useEnv(SUPABASE_ENV);
    await pendingCapture.keep(fakeFs.cameraFile(), 'image/jpeg', '2026-09-26T10:00:00.000Z');
    pendingCapture.requestSave();
    const view = await renderSettled(Passport);
    expect(view.queryByText('Continue as Dev User')).toBeNull();
    expect(createSupabaseClient).toHaveBeenCalledTimes(1);

    const otpControls = controller as unknown as { verifyOtp: jest.Mock; requestOtp: jest.Mock };
    otpControls.requestOtp.mockResolvedValue(undefined);
    otpControls.verifyOtp.mockResolvedValue(complete);
    await fireEvent.changeText(view.getByPlaceholderText('you@example.com'), 'me@example.com');
    await fireEvent.press(view.getByText('Email me a code'));
    await fireEvent.changeText(await view.findByPlaceholderText('6-digit code'), '123456');
    await fireEvent.press(view.getByText('Verify code'));
    await waitFor(() => expect(dismissTo).toHaveBeenCalledWith('/scan'));
  });
});
