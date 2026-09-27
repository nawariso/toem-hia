import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { router, useFocusEffect, useIsFocused } from 'expo-router';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { ActivityIndicator, Image, Linking, Pressable, StyleSheet, Text, View } from 'react-native';
import { useAuth } from '../src/auth/AuthContext';
import { GUIDANCE, MOTTO } from '../src/capture/guidance';
import { pendingCapture } from '../src/capture/pending-capture';
import { CaptureSaveError, savePendingCapture, type SaveStage } from '../src/capture/save-flow';

type SavePhase =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'failed'; stage: SaveStage; message: string }
  | { kind: 'saved' };

const FAILURE_TEXT: Record<SaveStage, string> = {
  create: 'Could not start the encounter. Your photo is kept on this device.',
  upload: 'Upload failed. Your photo is kept on this device.',
  submit: 'Your photo is uploaded, but the encounter is not submitted yet.',
};

function usePendingCapture() {
  return useSyncExternalStore(pendingCapture.subscribe, pendingCapture.get, pendingCapture.get);
}

export default function Scan() {
  const { state, encounters } = useAuth();
  const focused = useIsFocused();
  const capture = usePendingCapture();
  const [permission, requestPermission] = useCameraPermissions();
  const [requesting, setRequesting] = useState(false);
  const [cameraReady, setCameraReady] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [captureError, setCaptureError] = useState<string | null>(null);
  const [phase, setPhase] = useState<SavePhase>({ kind: 'idle' });
  const camera = useRef<CameraView>(null);
  const busy = useRef(false);

  const authenticated = state.status === 'AUTHENTICATED' && state.user !== null;
  const profileComplete = authenticated && state.user?.profileComplete === true;

  // Exactly one CameraView, and only while this screen is focused and not
  // showing a preview or a save result.
  const cameraMounted = focused && permission?.granted === true && !capture && phase.kind !== 'saved';
  // Readiness belongs to one mounted camera: it is dropped whenever the screen
  // loses focus, so a remounted camera must report onCameraReady again.
  useFocusEffect(useCallback(() => () => setCameraReady(false), []));

  const runSave = useCallback(async () => {
    if (busy.current) return;
    if (!encounters) {
      setPhase({ kind: 'failed', stage: 'create', message: 'App configuration is incomplete' });
      return;
    }
    busy.current = true;
    setPhase({ kind: 'saving' });
    try {
      await savePendingCapture(encounters);
      setPhase({ kind: 'saved' });
    } catch (cause) {
      const stage = cause instanceof CaptureSaveError ? cause.stage : 'create';
      setPhase({ kind: 'failed', stage, message: FAILURE_TEXT[stage] });
    } finally {
      busy.current = false;
    }
  }, [encounters]);

  // Resume a save the user already asked for (for example after signing in
  // from this capture's Save button). Deferred one tick and cancelled if the
  // screen loses focus before it runs.
  useEffect(() => {
    if (!(focused && profileComplete && capture?.saveRequested && phase.kind === 'idle')) return;
    const timer = setTimeout(() => void runSave(), 0);
    return () => clearTimeout(timer);
  }, [focused, profileComplete, capture, phase.kind, runSave]);

  async function askPermission() {
    setRequesting(true);
    try {
      await requestPermission();
    } finally {
      setRequesting(false);
    }
  }

  async function takePhoto() {
    if (!cameraMounted || !cameraReady || capturing || !camera.current) return;
    setCapturing(true);
    setCaptureError(null);
    const capturedAt = new Date().toISOString();
    try {
      const picture = await camera.current.takePictureAsync({ quality: 0.85, exif: false, base64: false, imageType: 'jpg' });
      if (!picture?.uri) throw new Error('No photo was returned');
      await pendingCapture.keep(picture.uri, picture.format === 'png' ? 'image/png' : 'image/jpeg', capturedAt);
      setCameraReady(false);
      setPhase({ kind: 'idle' });
    } catch {
      setCaptureError('Capture failed. Try again.');
    } finally {
      setCapturing(false);
    }
  }

  function retake() {
    pendingCapture.discard();
    setPhase({ kind: 'idle' });
  }

  function cancel() {
    pendingCapture.discard();
    router.replace('/home');
  }

  function save() {
    if (busy.current || phase.kind === 'saving') return;
    pendingCapture.requestSave();
    if (!authenticated) {
      router.push('/passport');
      return;
    }
    if (!profileComplete) {
      router.push('/passport-setup');
      return;
    }
    void runSave();
  }

  if (phase.kind === 'saved') {
    return (
      <View style={styles.screen}>
        <Text style={styles.title}>Encounter saved</Text>
        <Text style={styles.body}>Your encounter is saved in your Hia Passport. Only you can see the photo.</Text>
        <Text style={styles.motto}>{MOTTO}</Text>
        <Pressable style={styles.button} onPress={() => setPhase({ kind: 'idle' })}>
          <Text style={styles.buttonText}>Scan another Hia</Text>
        </Pressable>
        <Pressable onPress={() => router.replace('/home')}>
          <Text style={styles.link}>Done</Text>
        </Pressable>
      </View>
    );
  }

  if (capture) {
    const saving = phase.kind === 'saving';
    return (
      <View style={styles.screen}>
        {capture.photo ? (
          <Image testID="capture-preview" source={{ uri: capture.photo.uri }} style={styles.preview} resizeMode="contain" />
        ) : (
          <Text style={styles.body}>Photo uploaded.</Text>
        )}
        {!authenticated ? <Text style={styles.hint}>Save this Hia to your Passport</Text> : null}
        {phase.kind === 'failed' ? <Text style={styles.error}>{phase.message}</Text> : null}
        {saving ? (
          <View style={styles.row}>
            <ActivityIndicator color="#d96c39" />
            <Text style={styles.body}>Saving encounter…</Text>
          </View>
        ) : phase.kind === 'failed' ? (
          <Pressable style={styles.button} onPress={() => void runSave()}>
            <Text style={styles.buttonText}>Retry</Text>
          </Pressable>
        ) : (
          <Pressable style={styles.button} onPress={save}>
            <Text style={styles.buttonText}>Save Encounter</Text>
          </Pressable>
        )}
        {!saving && capture.photo && !capture.uploaded ? (
          <Pressable style={styles.secondary} onPress={retake}>
            <Text style={styles.secondaryText}>Retake</Text>
          </Pressable>
        ) : null}
        {!saving ? (
          <Pressable onPress={cancel}>
            <Text style={styles.link}>Cancel</Text>
          </Pressable>
        ) : null}
      </View>
    );
  }

  if (!permission) {
    return (
      <View style={styles.screen}>
        <ActivityIndicator color="#d96c39" />
        <Text style={styles.body}>Checking camera permission…</Text>
      </View>
    );
  }

  if (!permission.granted) {
    return (
      <View style={styles.screen}>
        <Text style={styles.title}>Camera access</Text>
        <Guidance />
        {permission.canAskAgain ? (
          <>
            <Text style={styles.body}>TOEM HIA needs the camera to photograph the Hia you meet.</Text>
            <Pressable disabled={requesting} style={[styles.button, requesting && styles.disabled]} onPress={askPermission}>
              {requesting ? <ActivityIndicator color="white" /> : <Text style={styles.buttonText}>Allow camera</Text>}
            </Pressable>
          </>
        ) : (
          <>
            <Text style={styles.error}>Camera access is turned off for TOEM HIA.</Text>
            <Pressable style={styles.button} onPress={() => void Linking.openSettings()}>
              <Text style={styles.buttonText}>Open Settings</Text>
            </Pressable>
          </>
        )}
      </View>
    );
  }

  const canCapture = cameraMounted && cameraReady && !capturing;
  return (
    <View style={styles.cameraScreen}>
      {cameraMounted ? (
        <CameraView
          ref={camera}
          style={styles.camera}
          facing="back"
          mode="picture"
          onCameraReady={() => setCameraReady(true)}
          onMountError={() => setCaptureError('The camera could not start.')}
        />
      ) : (
        <View style={styles.camera} />
      )}
      <View style={styles.panel}>
        <Guidance />
        {!cameraReady ? <Text style={styles.hint}>Starting camera…</Text> : null}
        {captureError ? <Text style={styles.error}>{captureError}</Text> : null}
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Take photo"
          disabled={!canCapture}
          style={[styles.button, !canCapture && styles.disabled]}
          onPress={takePhoto}
        >
          {capturing ? <ActivityIndicator color="white" /> : <Text style={styles.buttonText}>Take photo</Text>}
        </Pressable>
      </View>
    </View>
  );
}

function Guidance() {
  return (
    <View style={styles.guidance}>
      {GUIDANCE.map((line) => (
        <Text key={line} style={styles.guidanceLine}>
          {line}
        </Text>
      ))}
      <Text style={styles.motto}>{MOTTO}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, padding: 24, gap: 14, backgroundColor: '#f4f1e8' },
  cameraScreen: { flex: 1, backgroundColor: '#102f2a' },
  camera: { flex: 1 },
  panel: { padding: 20, gap: 10, backgroundColor: '#f4f1e8' },
  preview: { flex: 1, borderRadius: 16, backgroundColor: '#102f2a' },
  title: { fontSize: 28, fontWeight: '900', color: '#102f2a' },
  body: { fontSize: 16, color: '#46615a' },
  hint: { fontSize: 14, fontWeight: '700', color: '#245f53' },
  guidance: { gap: 2 },
  guidanceLine: { fontSize: 14, color: '#102f2a' },
  motto: { marginTop: 6, fontSize: 14, fontWeight: '900', color: '#d96c39' },
  row: { flexDirection: 'row', gap: 10, alignItems: 'center' },
  button: { padding: 17, borderRadius: 14, alignItems: 'center', backgroundColor: '#d96c39' },
  disabled: { opacity: 0.45 },
  buttonText: { fontWeight: '800', color: 'white', fontSize: 16 },
  secondary: { padding: 15, borderRadius: 14, borderWidth: 1, borderColor: '#245f53', alignItems: 'center' },
  secondaryText: { fontWeight: '800', color: '#245f53' },
  link: { textAlign: 'center', color: '#245f53', fontWeight: '700' },
  error: { color: '#a62d26' },
});
