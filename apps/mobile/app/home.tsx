import { router } from 'expo-router';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useAuth } from '../src/auth/AuthContext';

export default function Home() {
  const { state } = useAuth();
  const authenticated = state.status === 'AUTHENTICATED';

  return (
    <View style={styles.screen}>
      <View>
        <Text style={styles.eyebrow}>BANGKOK MONITOR LIZARDS</Text>
        <Text style={styles.title}>Meet the Hia around you.</Text>
        <Text style={styles.body}>Help build a respectful record of the city&apos;s wild neighbours.</Text>
      </View>
      <Pressable accessibilityRole="button" style={styles.card} onPress={() => router.push('/scan')}>
        <Text style={styles.cardTitle}>Scan a Hia</Text>
        <Text style={styles.coming}>Photograph a monitor lizard you meet.</Text>
      </Pressable>
      <Pressable style={styles.button} onPress={() => router.push(authenticated ? '/profile' : '/passport')}>
        <Text style={styles.buttonText}>{authenticated ? 'View Hia Passport' : 'Create Your Hia Passport'}</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, padding: 24, justifyContent: 'space-between', backgroundColor: '#f4f1e8' },
  eyebrow: { fontSize: 12, fontWeight: '800', letterSpacing: 1.5, color: '#d96c39' },
  title: { marginTop: 12, fontSize: 38, lineHeight: 43, fontWeight: '900', color: '#102f2a' },
  body: { marginTop: 14, fontSize: 17, lineHeight: 25, color: '#46615a' },
  card: { padding: 24, borderRadius: 18, backgroundColor: '#dce7df' },
  cardTitle: { fontSize: 24, fontWeight: '800', color: '#102f2a' },
  coming: { marginTop: 8, fontSize: 12, fontWeight: '800', letterSpacing: 1, color: '#6d827b' },
  button: { padding: 17, borderRadius: 14, backgroundColor: '#d96c39', alignItems: 'center' },
  buttonText: { color: 'white', fontWeight: '800', fontSize: 16 },
});
