import DateTimePicker, { type DateTimePickerEvent } from '@react-native-community/datetimepicker';
import { useState } from 'react';
import { Platform, StyleSheet, TouchableOpacity } from 'react-native';
import { TextInput } from '@/components/text-field';

import { ApiRequestError } from '@/api/client';
import { taskAction } from '@/api/endpoints';
import { confirmAction } from '@/confirm';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { TeamTaskDetail } from '@/types';
import { alert, locale } from '@/i18n';

const pad = (n: number) => String(n).padStart(2, '0');
const localTime = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
const shown = (at: string | Date) => new Date(at).toLocaleString(locale(), {
  weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
});

// What's being filled in before it's sent: a reason, a time, or both.
type Form =
  | { kind: 'delay' }
  | { kind: 'day_time' }
  | { kind: 'change' }
  | { kind: 'deny' }
  | { kind: 'rebook' };

const TITLES: Record<Form['kind'], string> = {
  delay: 'Tell the customer about a delay',
  day_time: 'The time for this day',
  change: 'Ask the manager to move the visit',
  deny: 'Refuse the change',
  rebook: 'Book a new visit',
};

/**
 * The website task page's other actions, from the app (api.views.TaskActionView):
 * customer notices, a day-only booking's time, schedule-change requests, a new
 * visit after a stop, the feedback request and reopening. Only what this person
 * can do now is offered; each asks first, as every task action does.
 */
export function TaskActionsPanel({ task, onChanged }: { task: TeamTaskDetail; onChanged: (task: TeamTaskDetail) => void }) {
  const theme = useTheme();
  const actions = task.actions;
  const [form, setForm] = useState<Form | null>(null);
  const [note, setNote] = useState('');
  const [when, setWhen] = useState<Date | null>(null);
  const [picking, setPicking] = useState<'date' | 'time' | 'datetime' | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState('');

  if (!actions) {
    return null;
  }
  const any = actions.notify_schedule || actions.notify_delay || actions.notify_shipping || actions.set_schedule_time
    || actions.request_schedule_change || actions.pending_change || actions.rebook || actions.send_feedback_request
    || actions.reopen;
  if (!any) {
    return null;
  }

  const run = async (body: Record<string, string | number>, question: [string, string], done: string) => {
    if (!(await confirmAction(question[0], question[1]))) {
      return;
    }
    setIsBusy(true);
    setError('');
    try {
      onChanged(await taskAction(task.id, body));
      setForm(null);
      alert('Done', done);
    } catch (err) {
      const body = err instanceof ApiRequestError ? (err.body as Record<string, unknown> | null) : null;
      const first = body && typeof body === 'object' ? Object.values(body).flat()[0] : null;
      setError(first ? String(first) : 'The request could not be completed. Please try again.');
    } finally {
      setIsBusy(false);
    }
  };

  const open = (next: Form) => {
    setForm(next);
    setNote('');
    setError('');
    const start = new Date();
    start.setDate(start.getDate() + 1);
    start.setHours(9, 0, 0, 0);
    setWhen(next.kind === 'day_time' && actions.scheduled_date ? new Date(`${actions.scheduled_date}T09:00:00`) : start);
  };

  const pickWhen = (kind: 'time' | 'datetime') => setPicking(kind === 'time' ? 'time' : Platform.OS === 'ios' ? 'datetime' : 'date');

  const submit = () => {
    if (!form) {
      return;
    }
    if (form.kind === 'delay') {
      run({ action: 'notify_delay', reason: note }, ['Tell the customer about a delay?', 'An email goes to the customer with your reason.'], 'The customer has been told about the delay.');
    } else if (form.kind === 'day_time' && when) {
      run({ action: 'set_schedule_time', time: `${pad(when.getHours())}:${pad(when.getMinutes())}` }, ['Set the time?', 'The customer is told the visit is booked.'], 'Time set.');
    } else if (form.kind === 'change' && when) {
      run({ action: 'request_schedule_change', requested_for: localTime(when), reason: note }, ['Ask to move the visit?', 'A manager reviews it.'], 'Change requested. A manager will review it.');
    } else if (form.kind === 'deny' && actions.pending_change) {
      run({ action: 'deny_schedule_change', request_id: actions.pending_change.id, note }, ['Refuse the change?', 'The visit stays at its time.'], 'Schedule change refused.');
    } else if (form.kind === 'rebook' && when) {
      run({ action: 'rebook', scheduled_for: localTime(when), note }, ['Book a new visit?', 'The customer and the lead are told.'], 'New visit booked.');
    }
  };

  const needsTime = form && form.kind !== 'delay' && form.kind !== 'deny';
  const needsNote = form && form.kind !== 'day_time';

  return (
    <ThemedView style={styles.section}>
      <ThemedText type="smallBold">Actions</ThemedText>
      {!actions.has_contact_email && (actions.notify_schedule || actions.notify_delay || actions.notify_shipping) ? (
        <ThemedText type="small" themeColor="danger">
          This branch has no contact email, so the customer can&apos;t be emailed. Add one on the website.
        </ThemedText>
      ) : null}

      {actions.pending_change ? (
        <ThemedView style={[styles.box, { borderColor: theme.primary }]}>
          <ThemedText type="smallBold">Change requested by {actions.pending_change.requested_by}</ThemedText>
          <ThemedText type="small">To: {shown(actions.pending_change.requested_for)}</ThemedText>
          {actions.pending_change.reason ? <ThemedText type="small" themeColor="textSecondary">{actions.pending_change.reason}</ThemedText> : null}
          {actions.pending_change.can_review ? (
            <ThemedView style={styles.row}>
              <Button label="Approve" disabled={isBusy} onPress={() => run(
                { action: 'approve_schedule_change', request_id: actions.pending_change!.id },
                ['Approve the change?', 'The visit moves and the customer is told.'], 'Schedule change approved.',
              )} />
              <Button label="Refuse" disabled={isBusy} onPress={() => open({ kind: 'deny' })} />
            </ThemedView>
          ) : <ThemedText type="small" themeColor="textSecondary">Waiting for a manager.</ThemedText>}
        </ThemedView>
      ) : null}

      <ThemedView style={styles.row}>
        {actions.notify_schedule ? (
          <Button
            label={actions.schedule_notified_at ? 'Tell the customer the new time' : 'Tell the customer the time'}
            disabled={isBusy || !actions.has_contact_email}
            onPress={() => run({ action: 'notify_schedule' }, ['Email the customer the visit time?', 'They get the day and time of the visit.'], 'The customer has been told the visit time.')}
          />
        ) : null}
        {actions.notify_delay ? (
          <Button label="Tell the customer about a delay" disabled={isBusy || !actions.has_contact_email} onPress={() => open({ kind: 'delay' })} />
        ) : null}
        {actions.notify_shipping ? (
          <Button
            label="Send the customer the tracking number" disabled={isBusy || !actions.has_contact_email}
            onPress={() => run({ action: 'notify_shipping' }, ['Email the customer the tracking number?', 'They get the courier and the tracking number.'], 'The customer has the tracking number.')}
          />
        ) : null}
        {actions.set_schedule_time ? <Button label="Set the time for the booked day" disabled={isBusy} onPress={() => open({ kind: 'day_time' })} /> : null}
        {actions.request_schedule_change ? <Button label="Ask to move the visit" disabled={isBusy} onPress={() => open({ kind: 'change' })} /> : null}
        {actions.rebook ? <Button label="Book a new visit" disabled={isBusy} onPress={() => open({ kind: 'rebook' })} /> : null}
        {actions.send_feedback_request ? (
          <Button
            label={actions.feedback_requested_at ? 'Ask for feedback again' : 'Ask the customer for feedback'}
            disabled={isBusy || !actions.has_contact_email}
            onPress={() => run({ action: 'send_feedback_request' }, ['Ask the customer for feedback?', 'They get an email with a link to rate the visit.'], 'The feedback request has been sent.')}
          />
        ) : null}
        {actions.reopen ? (
          <Button
            label="Reopen the task" disabled={isBusy}
            onPress={() => run({ action: 'reopen' }, ['Reopen this task?', 'It goes back to awaiting the manager.'], 'Task reopened.')}
          />
        ) : null}
      </ThemedView>
      {actions.schedule_notified_at ? (
        <ThemedText type="small" themeColor="textSecondary">Customer last told the time: {shown(actions.schedule_notified_at)}</ThemedText>
      ) : null}
      {actions.feedback_requested_at ? (
        <ThemedText type="small" themeColor="textSecondary">Feedback asked: {shown(actions.feedback_requested_at)}</ThemedText>
      ) : null}

      {form ? (
        <ThemedView style={[styles.box, { borderColor: theme.primary }]}>
          <ThemedText type="smallBold">{TITLES[form.kind]}</ThemedText>
          {needsTime ? (
            <Button
              label={when ? (form.kind === 'day_time' ? when.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' }) : shown(when)) : 'Pick the time'}
              onPress={() => pickWhen(form.kind === 'day_time' ? 'time' : 'datetime')}
            />
          ) : null}
          {needsNote ? (
            <TextInput
              style={[styles.input, { color: theme.text, borderColor: theme.backgroundSelected }]}
              value={note} onChangeText={setNote} multiline placeholderTextColor={theme.textSecondary}
              placeholder={form.kind === 'delay' ? 'Reason (the customer reads it)' : form.kind === 'deny' ? 'Why (optional)' : 'Note (optional)'}
            />
          ) : null}
          <ThemedView style={styles.row}>
            <Button label="Send" disabled={isBusy || (form.kind === 'delay' && !note.trim())} onPress={submit} />
            <Button label="Back" onPress={() => setForm(null)} />
          </ThemedView>
        </ThemedView>
      ) : null}
      {error ? <ThemedText themeColor="danger" type="small">{error}</ThemedText> : null}

      {picking && when ? (
        <DateTimePicker
          value={when} mode={picking} display={Platform.OS === 'ios' ? 'inline' : 'default'}
          onChange={(event: DateTimePickerEvent, date?: Date) => {
            if (event.type === 'dismissed' || !date) {
              setPicking(null);
              return;
            }
            const next = new Date(when);
            if (picking === 'time') {
              next.setHours(date.getHours(), date.getMinutes());
            } else if (picking === 'date') {
              next.setFullYear(date.getFullYear(), date.getMonth(), date.getDate());
            } else {
              next.setTime(date.getTime());
            }
            setWhen(next);
            setPicking(picking === 'date' ? 'time' : null);
          }}
        />
      ) : null}
    </ThemedView>
  );
}

function Button({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) {
  const theme = useTheme();
  return (
    <TouchableOpacity
      style={[styles.button, { borderColor: theme.primary }, disabled && styles.disabled]}
      onPress={onPress} disabled={disabled}
    >
      <ThemedText type="small" themeColor="primary">{label}</ThemedText>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  section: { gap: Spacing.two, backgroundColor: 'transparent' },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  box: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.two },
  input: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, minHeight: 60, textAlignVertical: 'top' },
  button: { borderWidth: 1, borderRadius: Spacing.two, paddingVertical: Spacing.two, paddingHorizontal: Spacing.three },
  disabled: { opacity: 0.5 },
});
