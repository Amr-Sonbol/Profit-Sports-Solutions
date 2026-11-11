import { Stack, useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';

import { ApiRequestError } from '@/api/client';
import { createInternalTicket, fetchNewInternalTicketForm, uploadInternalTicketFile } from '@/api/endpoints';
import { ChoicePicker } from '@/components/choice-picker';
import { PickedFiles } from '@/components/picked-files';
import { TextInput } from '@/components/text-field';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { alert, t } from '@/i18n';
import type { PickedFile } from '@/pick-files';
import type { Choice } from '@/types';

/**
 * A new internal task — the website's form: a subject, the details, who it's
 * for (they're told on their phone) and any photos or files.
 */
export default function NewInternalTaskScreen() {
  const router = useRouter();
  const theme = useTheme();
  const [people, setPeople] = useState<Choice[] | null>(null);
  const [loadError, setLoadError] = useState('');
  const [subject, setSubject] = useState('');
  const [description, setDescription] = useState('');
  const [assignedTo, setAssignedTo] = useState('');
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetchNewInternalTicketForm().then((form) => {
      setPeople(form.people.map((person) => ({ value: String(person.value), label: person.label })));
    }).catch(() => setLoadError('Could not load the form. Check your connection and try again.'));
  }, []);

  const save = async () => {
    const missing: Record<string, string> = {};
    if (!subject.trim()) {
      missing.subject = 'This field is required.';
    }
    if (!description.trim()) {
      missing.description = 'This field is required.';
    }
    if (!assignedTo) {
      missing.assigned_to = 'This field is required.';
    }
    setErrors(missing);
    if (Object.keys(missing).length) {
      return;
    }
    setBusy(true);
    try {
      const ticket = await createInternalTicket({
        subject: subject.trim(), description: description.trim(), assigned_to: Number(assignedTo),
      });
      // The files follow, one at a time; one that fails can be added from the task as a note.
      const failed: string[] = [];
      for (const file of files) {
        try {
          await uploadInternalTicketFile(ticket.id, file.uri, file.mimeType);
        } catch {
          failed.push(file.name);
        }
      }
      if (failed.length) {
        alert('Some files were not sent', t('Add them to the task as a note: {names}', { names: failed.join(', ') }));
      }
      router.replace(`/internal-task/${ticket.id}`);
    } catch (err) {
      if (err instanceof ApiRequestError && err.status === 400 && err.body && typeof err.body === 'object') {
        setErrors(Object.fromEntries(Object.entries(err.body as Record<string, string[]>)
          .map(([field, messages]) => [field, Array.isArray(messages) ? messages.join(' ') : String(messages)])));
      } else {
        setErrors({ form: 'Could not save. Check your connection and try again.' });
      }
    } finally {
      setBusy(false);
    }
  };

  if (!people) {
    return (
      <ThemedView style={styles.center}>
        <Stack.Screen options={{ title: t('New internal task') }} />
        {loadError ? <ThemedText themeColor="danger">{loadError}</ThemedText> : <ActivityIndicator />}
      </ThemedView>
    );
  }

  const input = [styles.input, { color: theme.text, borderColor: theme.backgroundSelected }];
  const fieldError = (name: string) => (errors[name] ? <ThemedText type="small" themeColor="danger">{errors[name]}</ThemedText> : null);

  return (
    <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined} keyboardVerticalOffset={90}>
      <Stack.Screen options={{ title: t('New internal task') }} />
      <ScrollView contentContainerStyle={styles.page} keyboardShouldPersistTaps="handled">
        <ThemedText type="smallBold">Subject</ThemedText>
        <TextInput style={input} value={subject} onChangeText={setSubject} maxLength={200} />
        {fieldError('subject')}

        <ThemedText type="smallBold">Details</ThemedText>
        <TextInput style={[input, styles.tall]} value={description} onChangeText={setDescription} multiline />
        {fieldError('description')}

        <ChoicePicker
          label="For" placeholder="Choose…" choices={people} value={assignedTo} onChange={setAssignedTo}
          error={errors.assigned_to}
        />
        <ThemedText type="small" themeColor="textSecondary">
          The person responsible for handling it. They will be notified, and it will appear in their internal tasks.
        </ThemedText>

        <ThemedText type="smallBold">Photos and files</ThemedText>
        <PickedFiles files={files} onChange={setFiles} disabled={busy} />

        {fieldError('form')}
        <TouchableOpacity
          style={[styles.button, { backgroundColor: theme.primary }, busy && styles.dim]} onPress={save} disabled={busy}
        >
          {busy ? <ActivityIndicator color={theme.onPrimary} /> : (
            <ThemedText type="smallBold" style={{ color: theme.onPrimary }}>Create internal task</ThemedText>
          )}
        </TouchableOpacity>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: Spacing.four },
  page: { padding: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  input: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, fontSize: 16 },
  tall: { minHeight: 110, textAlignVertical: 'top' },
  button: { borderRadius: Spacing.two, paddingVertical: Spacing.three, alignItems: 'center', marginTop: Spacing.two },
  dim: { opacity: 0.4 },
});
