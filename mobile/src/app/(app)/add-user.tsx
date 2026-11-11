import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { TextInput } from '@/components/text-field';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { createPerson, fetchNewPersonForm } from '@/api/endpoints';
import { ChoicePicker } from '@/components/choice-picker';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { FormFieldSpec } from '@/types';
import { alert, t } from '@/i18n';

/**
 * More → Add a user (the admin only) — the website's one-step Add user:
 * role, name, the country they work in, their email (their login: they
 * get a link to choose their own password), phone, language, employment.
 */
export default function AddUserScreen() {
  const router = useRouter();
  const theme = useTheme();
  const [fields, setFields] = useState<Record<string, FormFieldSpec> | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [isBusy, setIsBusy] = useState(false);

  useFocusEffect(useCallback(() => {
    fetchNewPersonForm().then(({ fields: specs }) => {
      setFields(specs);
      setValues(Object.fromEntries(Object.entries(specs).map(([name, spec]) => [name, spec.initial])));
      setErrors({});
    }).catch((err) => setErrors({ form: err instanceof ApiRequestError ? err.message : 'Could not load the form.' }));
  }, []));

  if (!fields) {
    return (
      <ThemedView style={styles.centered}>
        {errors.form ? <ThemedText themeColor="danger">{errors.form}</ThemedText> : <ActivityIndicator />}
      </ThemedView>
    );
  }

  const set = (name: string) => (value: string) => setValues((current) => ({ ...current, [name]: value }));
  const inputStyle = [styles.input, { color: theme.text, borderColor: theme.backgroundSelected }];
  const picker = (name: string) => (
    <ChoicePicker
      label={fields[name].label} choices={fields[name].choices ?? []} value={values[name] ?? ''}
      onChange={set(name)} error={errors[name]}
    />
  );
  const text = (name: string, options: { keyboardType?: 'email-address' | 'phone-pad'; hint?: string } = {}) => (
    <ThemedView style={styles.field}>
      <ThemedText type="smallBold">{fields[name].label}{fields[name].required ? '' : ' (optional)'}</ThemedText>
      <TextInput
        style={inputStyle} value={values[name] ?? ''} onChangeText={set(name)}
        keyboardType={options.keyboardType} autoCapitalize={options.keyboardType === 'email-address' ? 'none' : 'words'}
        autoCorrect={false}
      />
      {options.hint ? <ThemedText type="small" themeColor="textSecondary">{options.hint}</ThemedText> : null}
      {errors[name] ? <ThemedText type="small" themeColor="danger">{errors[name]}</ThemedText> : null}
    </ThemedView>
  );

  const save = async () => {
    setIsBusy(true);
    setErrors({});
    try {
      const created = await createPerson(values);
      alert(
        t('{name} added', { name: created.name }),
        created.has_login
          ? 'They get an email with a link to choose their own password.'
          : 'No email yet, so they cannot sign in. Add their email on their page when you have it.',
        [{ text: 'OK', onPress: () => router.replace({ pathname: '/people/[id]', params: { id: String(created.id) } }) }],
      );
    } catch (err) {
      if (err instanceof ApiRequestError && err.status === 400 && err.body && typeof err.body === 'object') {
        const body = err.body as Record<string, string[]>;
        setErrors(Object.fromEntries(Object.entries(body).map(([field, messages]) => [field === '__all__' ? 'form' : field, messages.join(' ')])));
      } else {
        setErrors({ form: err instanceof ApiRequestError ? err.message : 'Not saved. Please try again.' });
      }
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.flex}>
          <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
            <TouchableOpacity onPress={() => router.back()}>
              <ThemedText themeColor="primary">← Back</ThemedText>
            </TouchableOpacity>
            <ThemedText type="title">Add a user</ThemedText>
            {picker('role')}
            {text('full_name')}
            {picker('country')}
            {text('email', { keyboardType: 'email-address', hint: 'Their login: they get a link to choose their own password.' })}
            {text('phone', { keyboardType: 'phone-pad' })}
            {picker('language')}
            {picker('employment_type')}
            {errors.form ? <ThemedText themeColor="danger">{errors.form}</ThemedText> : null}
            <TouchableOpacity style={[styles.saveButton, { backgroundColor: theme.primary }]} onPress={save} disabled={isBusy}>
              {isBusy ? <ActivityIndicator color={theme.onPrimary} /> : (
                <ThemedText type="smallBold" style={{ color: theme.onPrimary }}>Add user</ThemedText>
              )}
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
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: Spacing.four },
  content: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.six, gap: Spacing.three },
  field: { gap: Spacing.one, backgroundColor: 'transparent' },
  input: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three, fontSize: 16 },
  saveButton: { borderRadius: Spacing.two, padding: Spacing.three, alignItems: 'center' },
});
