import Ionicons from '@expo/vector-icons/Ionicons';
import { useCallback, useMemo, useState } from 'react';
import { Stack, useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { ActivityIndicator, KeyboardAvoidingView, Linking, Platform, RefreshControl, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { TextInput } from '@/components/text-field';

import { ApiRequestError } from '@/api/client';
import { decideTicket, fetchTicket, sendTicketMessage } from '@/api/endpoints';
import { ChoicePicker } from '@/components/choice-picker';
import { MediaStrip } from '@/components/customer-request';
import { OfflineNotice } from '@/components/offline-notice';
import { withCountryCopy } from '@/offline';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { confirmAction } from '@/confirm';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { ago } from '@/task-time';
import type { TicketDetail } from '@/types';
import { t } from '@/i18n';

type Choice = 'visit' | 'ship' | 'answer';
const CHOICES: [Choice, string][] = [['visit', 'Send a technician'], ['ship', 'Ship a part'], ['answer', 'Answer without a visit']];

function errorText(err: unknown, fallback: string) {
  if (err instanceof ApiRequestError && err.body && typeof err.body === 'object' && 'detail' in err.body) {
    return String((err.body as { detail: unknown }).detail);
  }
  return fallback;
}

/**
 * A customer request — the website's request page on the phone: who and where,
 * each machine with what they reported, photos and what's been decided, the
 * conversation and staff notes, an escalation, its tasks; then the daily work:
 * "What happens next?", a reply to the customer, a note for staff. Everything
 * rarer (escalate, assign, dismiss, edit, add their photos) is the website's
 * page, one tap away.
 */
export default function TicketScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const theme = useTheme();
  const router = useRouter();
  const [ticket, setTicket] = useState<TicketDetail | null>(null);
  const [error, setError] = useState('');
  // Showing the last copy kept on the phone (no connection).
  const [offline, setOffline] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  // "What happens next?"
  const [choice, setChoice] = useState<Choice>('visit');
  const [selected, setSelected] = useState<number[]>([]);
  const [supervisor, setSupervisor] = useState('');
  const [technician, setTechnician] = useState('');
  const [reason, setReason] = useState('');
  const [answer, setAnswer] = useState('');
  const [nextError, setNextError] = useState('');
  // The reply and the note.
  const [reply, setReply] = useState('');
  const [note, setNote] = useState('');

  const load = useCallback(async () => {
    try {
      const copy = await withCountryCopy(`ticket-${id}`, () => fetchTicket(Number(id)));
      const data = copy.data;
      setOffline(copy.offline);
      setTicket(data);
      // Every machine still waiting is chosen to start with, as on the website.
      setSelected(data.machines.filter((machine) => !machine.decision).map((machine) => machine.id));
      setError('');
    } catch (err) {
      setError(err instanceof ApiRequestError && err.status === 403
        ? 'You do not have permission to open this request.'
        : err instanceof ApiRequestError && err.status === 404 ? 'This request was not found.' : 'Could not load the request.');
    }
  }, [id]);

  useFocusEffect(useCallback(() => {
    load();
  }, [load]));

  const reasons = useMemo(() => (ticket?.next?.reasons ?? []).flatMap((group) => group.options.map((option) => ({
    value: option.value, label: `${group.group} — ${option.label}`, suggestion: option.suggestion,
  }))), [ticket]);

  const openWebsite = () => ticket && router.push({
    pathname: '/website', params: { path: ticket.website_path, title: ticket.ticket_number },
  });

  const decide = async () => {
    if (!ticket?.next) {
      return;
    }
    setNextError('');
    const waiting = ticket.machines.filter((machine) => !machine.decision);
    if (waiting.length && !selected.length) {
      setNextError('Choose at least one machine.');
      return;
    }
    if (choice === 'answer' && !reason) {
      setNextError('Please choose why.');
      return;
    }
    if (choice === 'answer' && !answer.trim()) {
      setNextError('Please write your answer to the customer.');
      return;
    }
    const what = choice === 'answer'
      ? ['Send this answer?', 'The customer gets it by email and on their request page. When no machine is left waiting, the request closes.']
      : [choice === 'ship' ? 'Create a part shipment?' : 'Create the visit?',
        supervisor ? 'A task is created and the supervisor is told.' : 'A task is created; you are its owner until you hand it over.'];
    if (!(await confirmAction(what[0], what[1], choice === 'answer' ? 'Send' : 'Create'))) {
      return;
    }
    setBusy(true);
    try {
      const result = await decideTicket(ticket.id, {
        choice, machines: selected,
        ...(choice === 'answer' ? { reason, answer: answer.trim() } : {
          supervisor: supervisor ? Number(supervisor) : '', technician: technician ? Number(technician) : '',
        }),
      });
      setNotice(result.message);
      setAnswer('');
      setReason('');
      if (result.task) {
        router.push(`/team-task/${result.task.id}`);
      }
      await load();
    } catch (err) {
      setNextError(errorText(err, 'Could not save. Check your connection and try again.'));
    } finally {
      setBusy(false);
    }
  };

  const send = async (asNote: boolean) => {
    const text = (asNote ? note : reply).trim();
    if (!ticket || !text) {
      return;
    }
    if (!asNote && !(await confirmAction('Send this reply?', 'The customer gets it by email and on their request page.', 'Send'))) {
      return;
    }
    setBusy(true);
    try {
      const result = await sendTicketMessage(ticket.id, text, asNote);
      setNotice(result.message);
      (asNote ? setNote : setReply)('');
      await load();
    } catch (err) {
      setNotice(errorText(err, 'Not sent. Check your connection and try again.'));
    } finally {
      setBusy(false);
    }
  };

  if (!ticket) {
    return (
      <ThemedView style={styles.center}>
        <Stack.Screen options={{ title: t('Request') }} />
        {error ? <ThemedText themeColor="danger">{error}</ThemedText> : <ActivityIndicator />}
      </ThemedView>
    );
  }

  const waiting = ticket.machines.filter((machine) => !machine.decision);
  const input = [styles.input, { color: theme.text, borderColor: theme.backgroundSelected }];

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
        {/* Status and who it's from */}
        <ThemedView style={styles.row}>
          <ThemedText type="subtitle" style={styles.flex}>{ticket.customer_name}</ThemedText>
          <ThemedText type="smallBold" themeColor={ticket.escalated ? 'danger' : 'primary'}>
            {ticket.escalated ? 'Escalated' : ticket.status_display}
          </ThemedText>
        </ThemedView>
        <ThemedText type="small" themeColor="textSecondary">
          {[ticket.about, ticket.need].filter(Boolean).join(' · ')} · received {ago(ticket.submitted_at)}
          {ticket.sent_by ? ` · ${t('sent by {name}', { name: ticket.sent_by })}` : ''}
        </ThemedText>
        <OfflineNotice offline={offline} />
        {notice ? <ThemedText themeColor="primary">{notice}</ThemedText> : null}

        <ThemedView style={[styles.card, { borderColor: theme.backgroundSelected }]}>
          <ThemedText type="smallBold">{ticket.site_name}{ticket.has_branch ? '' : ' (not one of their branches yet)'}</ThemedText>
          {ticket.site_address ? <ThemedText type="small" themeColor="textSecondary">{ticket.site_address}</ThemedText> : null}
          {ticket.contact_name ? <ThemedText>{ticket.contact_name}</ThemedText> : null}
          {ticket.contact_phone ? (
            <TouchableOpacity onPress={() => Linking.openURL(`tel:${ticket.contact_phone}`)}>
              <ThemedText themeColor="primary">{ticket.contact_phone}</ThemedText>
            </TouchableOpacity>
          ) : null}
          {ticket.contact_email ? (
            <TouchableOpacity onPress={() => Linking.openURL(`mailto:${ticket.contact_email}`)}>
              <ThemedText themeColor="primary">{ticket.contact_email}</ThemedText>
            </TouchableOpacity>
          ) : null}
        </ThemedView>

        {ticket.escalation ? (
          <ThemedView style={[styles.card, { borderColor: theme.danger }]}>
            <ThemedText type="smallBold" themeColor="danger">Escalated to {ticket.escalation.to}</ThemedText>
            <ThemedText type="small">By {ticket.escalation.by}, {ago(ticket.escalation.at)}: {ticket.escalation.reason}</ThemedText>
          </ThemedView>
        ) : null}

        {/* What they reported */}
        <ThemedText type="smallBold" style={styles.section}>The request</ThemedText>
        {ticket.subject ? <ThemedText type="smallBold">{ticket.subject}</ThemedText> : null}
        {ticket.message ? <ThemedText>{ticket.message}</ThemedText> : null}
        {ticket.machines.map((machine) => (
          <ThemedView key={machine.id} style={[styles.card, { borderColor: theme.backgroundSelected }]}>
            <ThemedText type="smallBold">{machine.name}{machine.serial ? ` · ${machine.serial}` : ''}</ThemedText>
            <ThemedText type="small" themeColor={machine.urgent ? 'danger' : undefined}>
              {machine.problem}{machine.urgent ? ' · urgent' : ''}
            </ThemedText>
            {machine.description ? <ThemedText type="small" themeColor="textSecondary">{machine.description}</ThemedText> : null}
            {machine.items.length ? <MediaStrip items={machine.items} /> : (
              <ThemedText type="small" themeColor="textSecondary">No photos sent for this machine.</ThemedText>
            )}
            {machine.decision ? (
              <TouchableOpacity disabled={!machine.task} onPress={() => machine.task && router.push(`/team-task/${machine.task.id}`)}>
                <ThemedText type="small" themeColor="primary">
                  {machine.decision_display}{machine.task ? ` · ${machine.task.task_number} ›` : ''}
                </ThemedText>
                {machine.decision_note && !machine.task ? <ThemedText type="small" themeColor="textSecondary">{machine.decision_note}</ThemedText> : null}
              </TouchableOpacity>
            ) : <ThemedText type="small" themeColor="textSecondary">Waiting for a decision</ThemedText>}
          </ThemedView>
        ))}
        {ticket.other_files.length ? (
          <>
            <ThemedText type="smallBold">Other files</ThemedText>
            <MediaStrip items={ticket.other_files} />
          </>
        ) : null}
        {ticket.tasks.length ? (
          <ThemedView style={styles.chips}>
            {ticket.tasks.map((task) => (
              <TouchableOpacity
                key={task.id} onPress={() => router.push(`/team-task/${task.id}`)}
                style={[styles.chip, { borderColor: theme.primary }]}
              >
                <ThemedText type="small" themeColor="primary">{task.task_number} · {task.status_display} ›</ThemedText>
              </TouchableOpacity>
            ))}
          </ThemedView>
        ) : null}
        {ticket.close_reason && ticket.status !== 'new' ? (
          <ThemedView style={[styles.card, { borderColor: theme.backgroundSelected }]}>
            <ThemedText type="smallBold">Our answer</ThemedText>
            <ThemedText type="small">{ticket.close_reason}</ThemedText>
          </ThemedView>
        ) : null}

        {/* What happens next? */}
        {ticket.next ? (
          <ThemedView style={[styles.panel, { backgroundColor: theme.backgroundElement }]}>
            <ThemedText type="subtitle">What happens next?</ThemedText>
            {waiting.length > 1 ? (
              <>
                <ThemedText type="small" themeColor="textSecondary">For which machines?</ThemedText>
                {waiting.map((machine) => {
                  const on = selected.includes(machine.id);
                  return (
                    <TouchableOpacity
                      key={machine.id} style={styles.check}
                      onPress={() => setSelected((current) => on ? current.filter((value) => value !== machine.id) : [...current, machine.id])}
                    >
                      <Ionicons name={on ? 'checkbox' : 'square-outline'} size={22} color={on ? theme.primary : theme.textSecondary} />
                      <ThemedText style={styles.flex}>{machine.name}{machine.serial ? ` · ${machine.serial}` : ''}</ThemedText>
                    </TouchableOpacity>
                  );
                })}
              </>
            ) : null}
            <ThemedView style={styles.choices}>
              {CHOICES.map(([value, label]) => (
                <TouchableOpacity
                  key={value} onPress={() => setChoice(value)}
                  style={[styles.choice, { borderColor: theme.backgroundSelected }, choice === value && { backgroundColor: theme.primary, borderColor: theme.primary }]}
                >
                  <ThemedText type="small" style={choice === value ? { color: theme.onPrimary } : undefined}>{label}</ThemedText>
                </TouchableOpacity>
              ))}
            </ThemedView>
            {choice === 'answer' ? (
              <>
                <ChoicePicker
                  label="Why" choices={reasons} value={reason} placeholder="Choose why…"
                  onChange={(value) => {
                    setReason(value);
                    // The suggested answer, in the customer's language — theirs to change.
                    const picked = reasons.find((option) => option.value === value);
                    if (picked) {
                      setAnswer(picked.suggestion);
                    }
                  }}
                />
                <ThemedText type="smallBold">Answer to the customer</ThemedText>
                <TextInput
                  style={[input, styles.tall]} value={answer} onChangeText={setAnswer} multiline
                  placeholder="Your answer" placeholderTextColor={theme.textSecondary}
                />
              </>
            ) : (
              <>
                {ticket.has_branch ? null : (
                  <ThemedText type="small" themeColor="danger">
                    This request has no branch yet: choose one on the website page first (More options).
                  </ThemedText>
                )}
                <ThemedText type="small" themeColor="textSecondary">
                  {choice === 'ship' ? 'Part shipment' : ticket.next.task_type || 'Visit'}
                  {ticket.next.urgent ? ' · high priority (a machine is not working)' : ''}
                  {ticket.next.under_warranty ? ' · under warranty' : ''}
                </ThemedText>
                <ChoicePicker
                  label="Supervisor (plans it)" optional placeholder="Me — I plan it"
                  choices={ticket.next.supervisors.map((person) => ({ value: String(person.value), label: person.label }))}
                  value={supervisor} onChange={setSupervisor}
                />
                {ticket.next.technicians ? (
                  <ChoicePicker
                    label="Technician" optional placeholder="The supervisor decides"
                    choices={ticket.next.technicians.map((person) => ({ value: String(person.value), label: person.label }))}
                    value={technician} onChange={setTechnician}
                  />
                ) : null}
              </>
            )}
            {nextError ? <ThemedText themeColor="danger">{nextError}</ThemedText> : null}
            <TouchableOpacity
              style={[styles.button, { backgroundColor: theme.primary }, busy && styles.dim]} onPress={decide} disabled={busy}
            >
              <ThemedText type="smallBold" style={{ color: theme.onPrimary }}>
                {choice === 'answer' ? 'Send the answer' : choice === 'ship' ? 'Create the shipment' : 'Create the visit'}
              </ThemedText>
            </TouchableOpacity>
          </ThemedView>
        ) : null}

        {/* The conversation with the customer */}
        <ThemedText type="smallBold" style={styles.section}>Conversation</ThemedText>
        {ticket.conversation.length ? ticket.conversation.map((entry, index) => (
          <ThemedView
            key={index}
            style={[styles.bubble, entry.from_us
              ? { backgroundColor: theme.backgroundSelected, alignSelf: 'flex-end' }
              : { borderColor: theme.backgroundSelected, borderWidth: 1, alignSelf: 'flex-start' }]}
          >
            <ThemedText type="small" themeColor="textSecondary">{entry.from_us ? entry.name || 'Us' : entry.name || 'Customer'} · {ago(entry.sent_at)}</ThemedText>
            <ThemedText>{entry.message}</ThemedText>
            {entry.files.length ? <MediaStrip items={entry.files} /> : null}
          </ThemedView>
        )) : <ThemedText type="small" themeColor="textSecondary">No messages yet.</ThemedText>}
        {ticket.can_reply ? (
          <ThemedView style={styles.sendRow}>
            <TextInput
              style={[input, styles.flex]} value={reply} onChangeText={setReply} multiline
              placeholder="Reply to the customer" placeholderTextColor={theme.textSecondary}
            />
            <TouchableOpacity onPress={() => send(false)} disabled={busy || !reply.trim()} style={!reply.trim() ? styles.dim : undefined}>
              <Ionicons name="send" size={24} color={theme.primary} />
            </TouchableOpacity>
          </ThemedView>
        ) : null}

        {/* Staff only */}
        {ticket.can_manage ? (
          <>
            <ThemedText type="smallBold" style={styles.section}>Internal notes (staff only)</ThemedText>
            {ticket.notes.map((entry, index) => (
              <ThemedView key={index} style={[styles.card, { borderColor: theme.backgroundSelected }]}>
                <ThemedText type="small" themeColor="textSecondary">{entry.name} · {ago(entry.created_at)}</ThemedText>
                <ThemedText>{entry.message}</ThemedText>
                {entry.files.length ? <MediaStrip items={entry.files} /> : null}
              </ThemedView>
            ))}
            <ThemedView style={styles.sendRow}>
              <TextInput
                style={[input, styles.flex]} value={note} onChangeText={setNote} multiline
                placeholder="A note for staff" placeholderTextColor={theme.textSecondary}
              />
              <TouchableOpacity onPress={() => send(true)} disabled={busy || !note.trim()} style={!note.trim() ? styles.dim : undefined}>
                <Ionicons name="add-circle" size={26} color={theme.primary} />
              </TouchableOpacity>
            </ThemedView>
          </>
        ) : null}

        <TouchableOpacity style={[styles.button, styles.outline, { borderColor: theme.backgroundSelected }]} onPress={openWebsite}>
          <ThemedText type="small">More options — escalate, assign, dismiss, edit, add photos, files ›</ThemedText>
        </TouchableOpacity>
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
  check: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two },
  choices: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  choice: { borderWidth: 1, borderRadius: 999, paddingHorizontal: Spacing.three, paddingVertical: Spacing.two },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  chip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: Spacing.three, paddingVertical: Spacing.one },
  input: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, fontSize: 16 },
  tall: { minHeight: 110, textAlignVertical: 'top' },
  sendRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two, backgroundColor: 'transparent' },
  bubble: { maxWidth: '88%', borderRadius: Spacing.two, padding: Spacing.two, gap: 2 },
  button: { borderRadius: Spacing.two, paddingVertical: Spacing.three, alignItems: 'center', marginTop: Spacing.two },
  outline: { borderWidth: 1, paddingHorizontal: Spacing.three },
  dim: { opacity: 0.4 },
});
