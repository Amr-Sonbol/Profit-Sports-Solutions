import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { PasswordForm } from '@/components/password-form';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useSession } from '@/context/auth';

/**
 * Shown instead of the app while someone is on a temporary password (a
 * new login, or a manager's reset) — the API refuses everything else
 * until they choose their own. Once saved, the root layout's guard moves
 * them on into the app by itself.
 */
export default function ChoosePasswordScreen() {
  const { me, signOut } = useSession();

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea}>
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.flex}>
          <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
            <ThemedText type="title" style={styles.title}>
              Choose your password
            </ThemedText>
            <ThemedText themeColor="textSecondary">
              {me?.full_name ? `${me.full_name}, you have` : 'You have'} signed in with a temporary password. Please choose your own password to continue.
            </ThemedText>

            <PasswordForm askCurrent={false} submitLabel="Save and continue" />

            <TouchableOpacity onPress={signOut} style={styles.signOut}>
              <ThemedText themeColor="textSecondary">Sign out</ThemedText>
            </TouchableOpacity>
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  flex: { flex: 1 },
  content: { padding: Spacing.four, gap: Spacing.three, flexGrow: 1, justifyContent: 'center' },
  title: { fontSize: 24 },
  signOut: { alignItems: 'center', paddingVertical: Spacing.two },
});
