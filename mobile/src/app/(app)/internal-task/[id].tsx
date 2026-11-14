import Ionicons from '@expo/vector-icons/Ionicons';
import { useCallback, useState } from 'react';
import { Stack, useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { ActivityIndicator, KeyboardAvoidingView, Platform, RefreshControl, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';

import { ApiRequestError } from '@/api/client';
import { actOnInternalTicket, fetchInternalTicket, uploadInternalTicketFile } from '@/api/endpoints';
import { ChoicePicker } from '@/components/choice-picker';
import { MediaStrip } from '@/components/customer-request';
import { OfflineNotice } from '@/components/offline-notice';
import { PickedFiles } from '@/components/picked-files';
import { TextInput } from '@/components/text-field';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { confirmAction } from '@/confirm';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { locale, t } from '@/i18n';
import { withCountryCopy } from '@/offline';
import type { PickedFile } from '@/pick-files';
import { ago } from '@/task-time';
import type { InternalTicketDetail } from '@/types';

function errorText(err: unknown, fallback: string) {
  if (err instanceof ApiRequestError && err.body && typeof err.body === 'object' && 'detail' in err.body) {
    return String((err.body as { detail: unknown }).detail);
  }
  return fallback;
}

/**
 * One internal task — work one staff member hands another — as on the
 * website's page: what it's about and who it's for,
 * its files, notes back and forth (with photos and files), handing it to
 * someone else, and closing it with the result. A manager can schedule it as
 * a field task (the website's form, one tap away).
 */
export default function InternalTaskScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const theme = useTheme();
  const router = useRouter();
  const [ticket, setTicket] = useState<InternalTicketDetail | null>(null);
  const [error, setError] = useState('');
  const [offline, setOffline] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [actionError, setActionError] = useState('');
  const [note, setNote] = useState('');
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [assignee, setAssignee] = useState('');
  const [result, setResult] = useState('');
  // A claim closes with its result (credit, replacement, rejected).
  const [outcome, setOutcome] = useState('');

  const load = useCallback(async () => {
    try {
      const copy = await withCountryCopy(`internal-${id}`, () => fetchInternalTicket(Number(id)));
      setOffline(copy.offline);
      setTicket(copy.data);
      setError('');
    } catch (err) {
      setError(err instanceof ApiRequestError && (err.status === 403 || err.status === 404)
        ? 'This internal task was not found.' : 'Could not load the internal task.');
    }
  }, [id]);

  useFocusEffect(useCallback(() => {
    load();
  }, [load]));

  const openWebsite = (path: string, title: string) => router.push({ pathname: '/website', params: { path, title } });

  const sendNote = async () => {
    if (!ticket || !note.trim()) {
      return;
    }
    setBusy(true);
    setActionError('');
    try {
      const saved = await actOnInternalTicket(ticket.id, { action: 'note', message: note.trim() });
      setTicket(saved);
      setNote('');
      // The files follow the note, one at a time.
      const left = [...files];
      while (left.length) {
        const file = left[0];
        try {
          setTicket(await uploadInternalTicketFile(ticket.id, file.uri, file.mimeType, saved.note_id));
          left.shift();
        } catch (err) {
          setFiles(left);
          setActionError(errorText(err, t('“{name}” was not sent. Check your connection and try again.', { name: file.name })));
          return;
        }
      }
      setFiles([]);
      setNotice('Note added.');
    } catch (err) {
      setActionError(errorText(err, 'Not sent. Check your connection and try again.'));
    } finally {
      setBusy(false);
    }
  };

  const act = async (input: Record<string, string | number>, title: string, message: string, button: string, done: string) => {
    if (!ticket || !(await confirmAction(title, message, button))) {
      return;
    }
    setBusy(true);
    setActionError('');
    try {
      setTicket(await actOnInternalTicket(ticket.id, input));
      setNotice(done);
      setAssignee('');
      setResult('');
    } catch (err) {
      setActionError(errorText(err, 'Could not save. Check your connection and try again.'));
    } finally {
      setBusy(false);
    }
  };

  if (!ticket) {
    return (
      <ThemedView style={styles.center}>
        <Stack.Screen options={{ title: t('Internal task') }} />
        {error ? <ThemedText themeColor="danger">{error}</ThemedText> : <ActivityIndicator />}
      </ThemedView>
    );
  }

  const input = [styles.input, { color: theme.text, borderColor: theme.backgroundSelected }];
  const others = ticket.people.filter((person) => person.value !== ticket.assigned_to?.id);

  return (
    <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined} keyboardVerticalOffset={90}>
      <Stack.Screen options={{ title: ticket.ticket_number }} />
      <ScrollView
        contentContainerStyle={styles.page} keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={isRefreshing} onRefresh={async () => {
          setIsRefreshing(true);
          await load();
          setIsRefreshing(false);
        }} />}
      >
        <ThemedView style={styles.row}>
          <ThemedText type="subtitle" style={styles.flex}>{ticket.subject}</ThemedText>
          <ThemedText type="smallBold" themeColor={ticket.is_open ? 'primary' : 'textSecondary'}>
            {ticket.is_open ? 'Open' : ticket.status === 'converted' ? 'Scheduled as a task' : 'Closed'}
          </ThemedText>
        </ThemedView>
        <ThemedText type="small" themeColor="textSecondary">
          {t('opened by {name}', { name: ticket.opened_by })} · {ago(ticket.submitted_at)}
        </ThemedText>
        <OfflineNotice offline={offline} />
        {notice ? <ThemedText themeColor="primary">{notice}</ThemedText> : null}

        <ThemedView style={[styles.card, { borderColor: theme.backgroundSelected }]}>
          <ThemedText type="small" themeColor="textSecondary">For</ThemedText>
          <ThemedText type="smallBold">{ticket.assigned_to?.name ?? '—'}</ThemedText>
          {ticket.linked_task ? (
            <TouchableOpacity onPress={() => router.push(`/team-task/${ticket.linked_task?.id}`)}>
              <ThemedText type="small" themeColor="primary">{t('About task {number}', { number: ticket.linked_task.task_number })}</ThemedText>
            </TouchableOpacity>
          ) : null}
          {ticket.due_on ? (
            <ThemedText type="small" themeColor={ticket.is_open && ticket.due_on < new Date().toISOString().slice(0, 10) ? 'danger' : 'textSecondary'}>
              {t('Due {date}', { date: new Date(ticket.due_on).toLocaleDateString(locale()) })}
            </ThemedText>
          ) : null}
          {ticket.waiting_for ? (
            <ThemedText type="small" themeColor="textSecondary">{t('Waiting for {who}', { who: ticket.waiting_for })}</ThemedText>
          ) : null}
          {ticket.is_claim ? (
            <ThemedText type="small" themeColor="textSecondary">{t('A claim: {result}', { result: ticket.claim_outcome || t('Open') })}</ThemedText>
          ) : null}
        </ThemedView>
        <ThemedText>{ticket.description}</ThemedText>
        {ticket.files.length ? <MediaStrip items={ticket.files} /> : null}

        {ticket.task ? (
          <TouchableOpacity
            onPress={() => ticket.task && router.push(`/team-task/${ticket.task.id}`)}
            style={[styles.chip, { borderColor: theme.primary }]}
          >
            <ThemedText type="small" themeColor="primary">{t('Field task {number}', { number: ticket.task.task_number })} ›</ThemedText>
          </TouchableOpacity>
        ) : null}
        {ticket.close_reason ? (
          <ThemedView style={[styles.card, { borderColor: theme.backgroundSelected }]}>
            <ThemedText type="smallBold">Result</ThemedText>
            <ThemedText type="small">{ticket.close_reason}</ThemedText>
          </ThemedView>
        ) : null}

        {/* Notes back and forth */}
        <ThemedText type="smallBold" style={styles.section}>Notes</ThemedText>
        {ticket.notes.length ? ticket.notes.map((entry) => (
          <ThemedView
            key={entry.id}
            style={[styles.bubble, entry.mine
              ? { backgroundColor: theme.backgroundSelected, alignSelf: 'flex-end' }
              : { borderColor: theme.backgroundSelected, borderWidth: 1, alignSelf: 'flex-start' }]}
          >
            <ThemedText type="small" themeColor="textSecondary">{entry.name} · {ago(entry.created_at)}</ThemedText>
            <ThemedText>{entry.message}</ThemedText>
            {entry.files.length ? <MediaStrip items={entry.files} /> : null}
          </ThemedView>
        )) : <ThemedText type="small" themeColor="textSecondary">No notes.</ThemedText>}
        {ticket.can_act ? (
          <>
            <ThemedView style={styles.sendRow}>
              <TextInput
                style={[input, styles.flex]} value={note} onChangeText={setNote} multiline
                placeholder="Write a note…" placeholderTextColor={theme.textSecondary}
              />
              <TouchableOpacity accessibilityLabel={t('Add note')} onPress={sendNote} disabled={busy || !note.trim()} style={!note.trim() ? styles.dim : undefined}>
                <Ionicons name="send" size={24} color={theme.primary} />
              </TouchableOpacity>
            </ThemedView>
            <PickedFiles files={files} onChange={setFiles} disabled={busy} />
          </>
        ) : null}
        {actionError ? <ThemedText themeColor="danger">{actionError}</ThemedText> : null}

        {ticket.can_act ? (
          <ThemedView style={[styles.panel, { backgroundColor: theme.backgroundElement }]}>
            <ThemedText type="smallBold">Hand it to someone else</ThemedText>
            <ChoicePicker
              label="Reassign to" placeholder="Choose…" value={assignee} onChange={setAssignee}
              choices={others.map((person) => ({ value: String(person.value), label: person.label }))}
            />
            <TouchableOpacity
              style={[styles.button, styles.outline, { borderColor: theme.primary }, (busy || !assignee) && styles.dim]}
              disabled={busy || !assignee}
              onPress={() => act(
                { action: 'reassign', assigned_to: Number(assignee) }, 'Hand it on?',
                t('{name} is told on their phone.', { name: others.find((person) => String(person.value) === assignee)?.label ?? '' }),
                'Reassign', 'Reassigned successfully.',
              )}
            >
              <ThemedText type="smallBold" themeColor="primary">Confirm reassignment</ThemedText>
            </TouchableOpacity>
          </ThemedView>
        ) : null}

        {ticket.can_act ? (
          <ThemedView style={[styles.panel, { backgroundColor: theme.backgroundElement }]}>
            <ThemedText type="smallBold">Close with the result</ThemedText>
            {ticket.is_claim ? (
              <ChoicePicker label="The claim's result" choices={ticket.claim_outcomes ?? []} value={outcome} onChange={setOutcome} />
            ) : null}
            <TextInput
              style={[input, styles.tall]} value={result} onChangeText={setResult} multiline
              placeholder="For example stock ordered, vendor responded or issue resolved." placeholderTextColor={theme.textSecondary}
            />
            <TouchableOpacity
              style={[styles.button, { backgroundColor: theme.primary }, (busy || !result.trim() || (ticket.is_claim && !outcome)) && styles.dim]}
              disabled={busy || !result.trim() || (ticket.is_claim && !outcome)}
              onPress={() => act(
                { action: 'close', result: result.trim(), claim_outcome: outcome }, 'Close this internal task?',
                'It can no longer be changed.', 'Close', 'Internal task closed',
              )}
            >
              <ThemedText type="smallBold" style={{ color: theme.onPrimary }}>Close internal task</ThemedText>
            </TouchableOpacity>
          </ThemedView>
        ) : null}

        {ticket.can_convert ? (
          <TouchableOpacity
            style={[styles.button, styles.outline, { borderColor: theme.backgroundSelected }]}
            onPress={() => openWebsite(`/tasks/new/?ticket=${ticket.id}`, t('Schedule as a task'))}
          >
            <ThemedText type="small">Schedule as a field task ›</ThemedText>
          </TouchableOpacity>
        ) : null}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: Spacing.four },
  page: { padding: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  row: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two, backgroundColor: 'transparent' },
  section: { marginTop: Spacing.three },
  card: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, gap: 2, backgroundColor: 'transparent' },
  panel: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.two, marginTop: Spacing.three },
  chip: { alignSelf: 'flex-start', borderWidth: 1, borderRadius: 999, paddingHorizontal: Spacing.three, paddingVertical: Spacing.one },
  input: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, fontSize: 16 },
  tall: { minHeight: 90, textAlignVertical: 'top' },
  sendRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two, backgroundColor: 'transparent' },
  bubble: { maxWidth: '88%', borderRadius: Spacing.two, padding: Spacing.two, gap: 2 },
  button: { borderRadius: Spacing.two, paddingVertical: Spacing.three, alignItems: 'center', marginTop: Spacing.one },
  outline: { borderWidth: 1, paddingHorizontal: Spacing.three },
  dim: { opacity: 0.4 },
});
