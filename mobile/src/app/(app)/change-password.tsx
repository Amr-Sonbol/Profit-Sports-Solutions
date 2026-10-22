import { useRouter } from 'expo-router';
import { useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { PasswordForm } from '@/components/password-form';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

/** Profile > Change password: needs the current one. Phones stay signed in. */
export default function ChangePasswordScreen() {
  const router = useRouter();
  const theme = useTheme();
  const [saved, setSaved] = useState(false);
  // A fresh form each visit, rather than the last visit's "saved" message.
  const [formKey, setFormKey] = useState(0);

  const backToProfile = () => {
    setSaved(false);
    setFormKey((key) => key + 1);
    router.navigate('/profile');
  };

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.flex}>
          <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
            <TouchableOpacity onPress={backToProfile}>
              <ThemedText themeColor="textSecondary">‹ Profile</ThemedText>
            </TouchableOpacity>
            <ThemedText type="title" style={styles.heading}>
              Change password
            </ThemedText>

            {saved ? (
              <>
                <ThemedText>Password changed. You stay signed in.</ThemedText>
                <TouchableOpacity style={[styles.button, { backgroundColor: theme.primary }]} onPress={backToProfile}>
                  <ThemedText style={[styles.buttonText, { color: theme.onPrimary }]}>Done</ThemedText>
                </TouchableOpacity>
              </>
            ) : (
              <>
                <PasswordForm key={formKey} askCurrent submitLabel="Save new password" onSaved={() => setSaved(true)} />
              </>
            )}
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
  content: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.four, gap: Spacing.three },
  heading: { fontSize: 22, paddingTop: Spacing.two },
  button: { borderRadius: Spacing.two, paddingVertical: Spacing.three, alignItems: 'center' },
  buttonText: { fontSize: 16, fontWeight: '600' },
});
