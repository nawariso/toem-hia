import { useEffect, useState } from 'react';
import { router } from 'expo-router';
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { useAuth } from '../src/auth/AuthContext';
import { routeAfterSignIn } from '../src/auth/state';
import { pendingCapture } from '../src/capture/pending-capture';

export default function Passport() {
  const { state, error, authMode, requestOtp, verifyOtp, signInDevelopmentUser } = useAuth();
  const [email, setEmail] = useState('');
  const [otp, setOtp] = useState('');
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (state.status !== 'AUTHENTICATED') return;
    // A guest who pressed Save on a capture goes back to that capture.
    const route = routeAfterSignIn(state, pendingCapture.get()?.saveRequested === true);
    if (route === '/scan') router.dismissTo('/scan');
    else router.replace(route);
  }, [state]);

  if (authMode === 'local') {
    return (
      <LocalDevLogin
        busy={busy}
        error={error}
        onContinue={async () => {
          setBusy(true);
          try {
            await signInDevelopmentUser();
          } catch {
            // The API error is surfaced through the auth context.
          } finally {
            setBusy(false);
          }
        }}
      />
    );
  }

  const disabled = busy || !email || (sent && !otp);

  async function submit() {
    setBusy(true);
    try {
      if (!sent) {
        await requestOtp(email);
        setSent(true);
      } else {
        await verifyOtp(email, otp);
      }
    } catch {
      // The provider error is surfaced through the auth context.
    } finally {
      setBusy(false);
    }
  }

  return (
    <View style={styles.screen}>
      <Text style={styles.title}>Create Your Hia Passport</Text>
      <Text style={styles.body}>
        {sent ? `Enter the code sent to ${email}` : 'Use your email. No password needed.'}
      </Text>
      <TextInput
        style={styles.input}
        value={email}
        onChangeText={setEmail}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="email-address"
        editable={!sent && !busy}
        placeholder="you@example.com"
      />
      {sent ? (
        <TextInput
          style={styles.input}
          value={otp}
          onChangeText={setOtp}
          keyboardType="number-pad"
          textContentType="oneTimeCode"
          placeholder="6-digit code"
          maxLength={8}
        />
      ) : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Pressable disabled={disabled} style={[styles.button, disabled && styles.disabled]} onPress={submit}>
        {busy ? <ActivityIndicator color="white" /> : <Text style={styles.buttonText}>{sent ? 'Verify code' : 'Email me a code'}</Text>}
      </Pressable>
      {sent ? (
        <Pressable
          onPress={() => {
            setSent(false);
            setOtp('');
          }}
        >
          <Text style={styles.link}>Use a different email</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

// LOCAL DEVELOPMENT ONLY. Rendered solely when the resolved auth mode is
// 'local', which resolveAuthConfig refuses for production or release builds.
function LocalDevLogin({ busy, error, onContinue }: { busy: boolean; error: string | null; onContinue(): void }) {
  return (
    <View style={styles.screen}>
      <View style={styles.devBanner}>
        <Text style={styles.devBannerText}>LOCAL DEVELOPMENT MODE</Text>
      </View>
      <Text style={styles.title}>Create Your Hia Passport</Text>
      <Text style={styles.body}>Sign in as the deterministic local development user. No email or code is sent.</Text>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Pressable disabled={busy} style={[styles.button, busy && styles.disabled]} onPress={onContinue}>
        {busy ? <ActivityIndicator color="white" /> : <Text style={styles.buttonText}>Continue as Dev User</Text>}
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, padding: 24, backgroundColor: '#f4f1e8' },
  devBanner: { alignSelf: 'flex-start', marginBottom: 16, paddingVertical: 6, paddingHorizontal: 10, borderRadius: 8, backgroundColor: '#f2c14e' },
  devBannerText: { fontSize: 11, fontWeight: '900', letterSpacing: 1.2, color: '#102f2a' },
  title: { fontSize: 32, fontWeight: '900', color: '#102f2a' },
  body: { marginTop: 10, fontSize: 16, color: '#46615a' },
  input: { marginTop: 22, borderWidth: 1, borderColor: '#9aada5', borderRadius: 12, padding: 15, fontSize: 17, backgroundColor: 'white' },
  button: { marginTop: 22, padding: 17, borderRadius: 14, alignItems: 'center', backgroundColor: '#d96c39' },
  disabled: { opacity: 0.45 },
  buttonText: { fontWeight: '800', color: 'white' },
  error: { marginTop: 14, color: '#a62d26' },
  link: { marginTop: 18, textAlign: 'center', color: '#245f53', fontWeight: '700' },
});
