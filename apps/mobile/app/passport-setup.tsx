import { useEffect, useState } from 'react';
import { router } from 'expo-router';
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { useAuth } from '../src/auth/AuthContext';
import { pendingCapture } from '../src/capture/pending-capture';

export default function PassportSetup() {
  const { state, error, updateProfile } = useAuth();
  const [username, setUsername] = useState(state.user?.username ?? '');
  const [displayName, setDisplayName] = useState(state.user?.displayName ?? '');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (state.status === 'GUEST') router.replace('/home');
    else if (state.user?.profileComplete) {
      // Finish a capture save the user started before setting up the passport.
      if (pendingCapture.get()?.saveRequested) router.dismissTo('/scan');
      else router.replace('/profile');
    }
  }, [state]);

  const disabled = busy || username.length < 3 || !displayName.trim();

  async function save() {
    setBusy(true);
    try {
      await updateProfile({ username, displayName, locale: state.user?.locale ?? 'th' });
    } catch {
      // The validation or conflict message is surfaced through the auth context.
    } finally {
      setBusy(false);
    }
  }

  return (
    <View style={styles.screen}>
      <Text style={styles.title}>Set up your passport</Text>
      <Text style={styles.body}>Choose how the TOEM HIA community will know you.</Text>
      <Text style={styles.label}>USERNAME</Text>
      <TextInput
        style={styles.input}
        value={username}
        onChangeText={setUsername}
        autoCapitalize="none"
        autoCorrect={false}
        placeholder="mickey"
      />
      <Text style={styles.hint}>3–30 letters, numbers, or underscores. Username is case-insensitively unique.</Text>
      <Text style={styles.label}>DISPLAY NAME</Text>
      <TextInput style={styles.input} value={displayName} onChangeText={setDisplayName} placeholder="Mickey" maxLength={80} />
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Pressable disabled={disabled} style={[styles.button, disabled && styles.disabled]} onPress={save}>
        {busy ? <ActivityIndicator color="white" /> : <Text style={styles.buttonText}>Create Hia Passport</Text>}
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, padding: 24, backgroundColor: '#f4f1e8' },
  title: { fontSize: 32, fontWeight: '900', color: '#102f2a' },
  body: { marginTop: 10, fontSize: 16, color: '#46615a' },
  label: { marginTop: 24, fontSize: 12, fontWeight: '800', letterSpacing: 1, color: '#46615a' },
  input: { marginTop: 8, borderWidth: 1, borderColor: '#9aada5', borderRadius: 12, padding: 15, fontSize: 17, backgroundColor: 'white' },
  hint: { marginTop: 7, fontSize: 12, lineHeight: 17, color: '#6d827b' },
  button: { marginTop: 28, padding: 17, borderRadius: 14, alignItems: 'center', backgroundColor: '#d96c39' },
  disabled: { opacity: 0.45 },
  buttonText: { fontWeight: '800', color: 'white' },
  error: { marginTop: 14, color: '#a62d26' },
});
