import { useState } from 'react';
import { ActivityIndicator, KeyboardAvoidingView, Platform, StyleSheet, TextInput, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useSession } from '@/context/auth';
import { useTheme } from '@/hooks/use-theme';

export default function SignInScreen() {
  const { signIn } = useSession();
  const theme = useTheme();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async () => {
    setError('');
    setIsSubmitting(true);
    try {
      await signIn(username.trim(), password);
    } catch (err) {
      // Same generic wording the web login uses, on purpose — this isn't
      // the place to hint at which of the two was wrong.
      setError(err instanceof ApiRequestError ? err.message : 'Could not sign in — check your connection.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea}>
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.form}>
          <ThemedText type="title" style={styles.title}>
            Profit Sports Solutions
          </ThemedText>
          <ThemedText type="subtitle" style={styles.subtitle}>
            Sign in
          </ThemedText>

          <TextInput
            style={[styles.input, { color: theme.text, borderColor: theme.backgroundSelected }]}
            placeholder="Username"
            placeholderTextColor={theme.textSecondary}
            autoCapitalize="none"
            autoCorrect={false}
            value={username}
            onChangeText={setUsername}
          />
          <TextInput
            style={[styles.input, { color: theme.text, borderColor: theme.backgroundSelected }]}
            placeholder="Password"
            placeholderTextColor={theme.textSecondary}
            secureTextEntry
            value={password}
            onChangeText={setPassword}
            onSubmitEditing={handleSubmit}
          />

          {error ? (
            <ThemedText themeColor="danger" style={styles.error}>
              {error}
            </ThemedText>
          ) : null}

          <TouchableOpacity
            style={[styles.button, { backgroundColor: theme.primary }, isSubmitting && styles.buttonDisabled]}
            onPress={handleSubmit}
            disabled={isSubmitting || !username || !password}
          >
            {isSubmitting ? <ActivityIndicator color={theme.onPrimary} /> : <ThemedText style={[styles.buttonText, { color: theme.onPrimary }]}>Sign in</ThemedText>}
          </TouchableOpacity>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1, justifyContent: 'center' },
  form: { paddingHorizontal: Spacing.four, gap: Spacing.three },
  title: { fontSize: 24, textAlign: 'center', marginBottom: Spacing.one },
  subtitle: { fontSize: 18, textAlign: 'center', marginBottom: Spacing.three },
  input: {
    borderWidth: 1,
    borderRadius: Spacing.two,
    paddingHorizontal: Spacing.three,
    paddingVertical: Spacing.three,
    fontSize: 16,
  },
  error: { textAlign: 'center' },
  button: {
    borderRadius: Spacing.two,
    paddingVertical: Spacing.three,
    alignItems: 'center',
    marginTop: Spacing.two,
  },
  buttonDisabled: { opacity: 0.6 },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '600' },
});
