import { Stack } from 'expo-router';
import { AuthProvider } from '../src/auth/AuthContext';

export default function RootLayout() {
  return <AuthProvider><Stack screenOptions={{ headerStyle: { backgroundColor: '#102f2a' }, headerTintColor: '#f4f1e8', contentStyle: { backgroundColor: '#f4f1e8' } }}><Stack.Screen name="index" options={{ headerShown: false }} /><Stack.Screen name="home" options={{ title: 'TOEM HIA' }} /><Stack.Screen name="scan" options={{ title: 'Scan a Hia' }} /><Stack.Screen name="passport" options={{ title: 'Create Your Hia Passport' }} /><Stack.Screen name="passport-setup" options={{ title: 'Passport Setup', gestureEnabled: false }} /><Stack.Screen name="profile" options={{ title: 'Hia Passport' }} /></Stack></AuthProvider>;
}
