import DateTimePicker, { type DateTimePickerEvent } from '@react-native-community/datetimepicker';
import * as DocumentPicker from 'expo-document-picker';
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import {
  ActivityIndicator, Alert, KeyboardAvoidingView, Linking, Platform, ScrollView, StyleSheet, Switch, TextInput,
  TouchableOpacity,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { type EditField, fetchTaskEditForm, saveTaskEdit, uploadFileTo } from '@/api/endpoints';
import { ChoicePicker } from '@/components/choice-picker';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { confirmAction } from '@/confirm';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

const pad = (n: number) => String(n).padStart(2, '0');
const toLocal = (d: Date, withTime: boolean) => (
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}${withTime ? `T${pad(d.getHours())}:${pad(d.getMinutes())}` : ''}`
);

/**
 * The website's Edit task, in the app: the same form, field by field as the
 * server describes it (api.views.TaskEditView) — so whatever the website's
 * form has, this has. Only what's changed is sent; a document is uploaded on
 * its own, straight away. The machines are planned on their own screen.
 */
export default function TaskEditScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const theme = useTheme();
  const [fields, setFields] = useState<EditField[] | null>(null);
  const [values, setValues] = useState<Record<string, string | boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [picking, setPicking] = useState<{ name: string; mode: 'date' | 'time' | 'datetime'; withTime: boolean } | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [loadError, setLoadError] = useState('');

  const load = useCallback(async () => {
    try {
      const form = await fetchTaskEditForm(Number(id));
      setFields(form.fields);
      setValues(Object.fromEntries(form.fields.map((field) => [field.name, field.value])));
    } catch (err) {
      setLoadError(err instanceof ApiRequestError ? err.message : 'Could not load the form.');
    }
  }, [id]);

  useFocusEffect(useCallback(() => {
    load();
  }, [load]));

  const errorsFrom = (err: unknown): Record<string, string> => {
    const body = err instanceof ApiRequestError ? (err.body as Record<string, unknown> | null) : null;
    if (!body || typeof body !== 'object') {
      return { form: 'Unable to save. Please try again.' };
    }
    return Object.fromEntries(Object.entries(body).map(([key, value]) => [key, String(Array.isArray(value) ? value[0] : value)]));
  };

  const save = async () => {
    if (!fields) {
      return;
    }
    const changed = Object.fromEntries(
      fields.filter((field) => field.kind !== 'file' && values[field.name] !== field.value).map((field) => [field.name, values[field.name]]),
    );
    if (!Object.keys(changed).length) {
      router.back();
      return;
    }
    if (!(await confirmAction('Save the changes?', 'They are recorded on the task; a new time tells the customer.'))) {
      return;
    }
    setIsBusy(true);
    setErrors({});
    try {
      const result = await saveTaskEdit(Number(id), changed);
      Alert.alert('Saved', [result.message, ...result.warnings].join('\n\n'));
      router.back();
    } catch (err) {
      setErrors(errorsFrom(err));
    } finally {
      setIsBusy(false);
    }
  };

  const upload = async (field: EditField) => {
    const picked = await DocumentPicker.getDocumentAsync({ type: ['application/pdf', 'image/*'], copyToCacheDirectory: true });
    if (picked.canceled || !picked.assets[0]) {
      return;
    }
    const file = picked.assets[0];
    if (!(await confirmAction(`Upload ${field.label}?`, file.name))) {
      return;
    }
    setIsBusy(true);
    setErrors({});
    try {
      await uploadFileTo(`/api/tasks/${id}/edit/`, file.uri, file.mimeType ?? 'application/pdf', field.name, {});
      Alert.alert('Uploaded', `${field.label}: ${file.name}`);
      await load();
    } catch (err) {
      setErrors(errorsFrom(err));
    } finally {
      setIsBusy(false);
    }
  };

  if (!fields) {
    return (
      <ThemedView style={styles.centered}>
        {loadError ? <ThemedText themeColor="danger">{loadError}</ThemedText> : <ActivityIndicator />}
      </ThemedView>
    );
  }

  const inputStyle = [styles.input, { color: theme.text, borderColor: theme.backgroundSelected }];
  const pickerValue = picking ? (values[picking.name] ? new Date(String(values[picking.name])) : new Date()) : new Date();

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backLink}>
          <ThemedText themeColor="primary">← Back</ThemedText>
        </TouchableOpacity>
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.safeArea}>
          <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
            <ThemedText type="subtitle">Edit task</ThemedText>
            {fields.map((field) => {
              const value = values[field.name];
              const error = errors[field.name];
              if (field.kind === 'choice') {
                return (
                  <ChoicePicker
                    key={field.name} label={field.label} choices={field.choices} value={String(value ?? '')}
                    onChange={(next) => setValues((all) => ({ ...all, [field.name]: next }))}
                    optional={!field.required} error={error}
                  />
                );
              }
              return (
                <ThemedView key={field.name} style={styles.field}>
                  <ThemedText type="smallBold">{field.label}</ThemedText>
                  {field.kind === 'bool' ? (
                    <Switch value={Boolean(value)} onValueChange={(next) => setValues((all) => ({ ...all, [field.name]: next }))} />
                  ) : field.kind === 'datetime' || field.kind === 'date' ? (
                    <ThemedView style={styles.row}>
                      <TouchableOpacity
                        style={[styles.button, { borderColor: theme.primary }]}
                        onPress={() => setPicking({
                          name: field.name, withTime: field.kind === 'datetime',
                          mode: field.kind === 'date' ? 'date' : Platform.OS === 'ios' ? 'datetime' : 'date',
                        })}
                      >
                        <ThemedText type="small" themeColor="primary">{value ? String(value).replace('T', ' ') : 'Pick'}</ThemedText>
                      </TouchableOpacity>
                      {value && !field.required ? (
                        <TouchableOpacity onPress={() => setValues((all) => ({ ...all, [field.name]: '' }))}>
                          <ThemedText type="small" themeColor="danger">Clear</ThemedText>
                        </TouchableOpacity>
                      ) : null}
                    </ThemedView>
                  ) : field.kind === 'file' ? (
                    <ThemedView style={styles.row}>
                      {value ? (
                        <TouchableOpacity onPress={() => Linking.openURL(String(value))}>
                          <ThemedText type="small" themeColor="primary">Open the current file</ThemedText>
                        </TouchableOpacity>
                      ) : <ThemedText type="small" themeColor="textSecondary">None yet</ThemedText>}
                      <TouchableOpacity style={[styles.button, { borderColor: theme.primary }]} onPress={() => upload(field)} disabled={isBusy}>
                        <ThemedText type="small" themeColor="primary">{value ? 'Replace' : 'Upload'}</ThemedText>
                      </TouchableOpacity>
                    </ThemedView>
                  ) : (
                    <TextInput
                      style={[inputStyle, field.kind === 'textarea' && styles.multiline]}
                      value={String(value ?? '')} multiline={field.kind === 'textarea'}
                      keyboardType={field.kind === 'number' ? 'decimal-pad' : 'default'}
                      onChangeText={(next) => setValues((all) => ({ ...all, [field.name]: next }))}
                    />
                  )}
                  {field.help ? <ThemedText type="small" themeColor="textSecondary">{field.help}</ThemedText> : null}
                  {error ? <ThemedText type="small" themeColor="danger">{error}</ThemedText> : null}
                </ThemedView>
              );
            })}
            {errors.form || errors.__all__ ? <ThemedText themeColor="danger">{errors.form ?? errors.__all__}</ThemedText> : null}
            <TouchableOpacity
              style={[styles.saveButton, { backgroundColor: theme.primary }, isBusy && styles.disabled]} onPress={save} disabled={isBusy}
            >
              <ThemedText style={{ color: theme.onPrimary }}>{isBusy ? 'Saving…' : 'Save'}</ThemedText>
            </TouchableOpacity>
          </ScrollView>
        </KeyboardAvoidingView>
        {picking ? (
          <DateTimePicker
            value={pickerValue} mode={picking.mode} display={Platform.OS === 'ios' ? 'inline' : 'default'}
            onChange={(event: DateTimePickerEvent, date?: Date) => {
              if (event.type === 'dismissed' || !date) {
                setPicking(null);
                return;
              }
              const next = new Date(pickerValue);
              if (picking.mode === 'time') {
                next.setHours(date.getHours(), date.getMinutes());
              } else if (picking.mode === 'date') {
                next.setFullYear(date.getFullYear(), date.getMonth(), date.getDate());
              } else {
                next.setTime(date.getTime());
              }
              setValues((all) => ({ ...all, [picking.name]: toLocal(next, picking.withTime) }));
              setPicking(picking.mode === 'date' && picking.withTime ? { ...picking, mode: 'time' } : null);
            }}
          />
        ) : null}
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  backLink: { paddingHorizontal: Spacing.three, paddingVertical: Spacing.two },
  content: { padding: Spacing.three, gap: Spacing.three, paddingBottom: Spacing.six },
  field: { gap: Spacing.one, backgroundColor: 'transparent' },
  row: { flexDirection: 'row', alignItems: 'center', gap: Spacing.three, backgroundColor: 'transparent' },
  input: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two },
  multiline: { minHeight: 80, textAlignVertical: 'top' },
  button: { borderWidth: 1, borderRadius: Spacing.two, paddingVertical: Spacing.one, paddingHorizontal: Spacing.three },
  saveButton: { borderRadius: Spacing.two, paddingVertical: Spacing.three, alignItems: 'center' },
  disabled: { opacity: 0.5 },
});
