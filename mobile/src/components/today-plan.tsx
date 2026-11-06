import { useState } from 'react';
import { useRouter } from 'expo-router';
import { ActivityIndicator, Alert, Linking, StyleSheet, TouchableOpacity } from 'react-native';

import { ApiRequestError } from '@/api/client';
import { sendTaskAction } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { ScheduleItem, TodayPlan } from '@/types';

// The lead's next tap — the same buttons as the task screen.
const ACTION_LABELS: Record<string, string> = {
  accept: 'Accept task',
  en_route: 'Heading to site',
  arrive: 'I have arrived',
  start: 'Start work',
};

const ROLE_LABELS: Record<string, string> = { lead: 'Lead', helper: 'Helper', responsible: 'Responsible' };

function time(value: string) {
  return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/** "09:00–11:00", "09:00", or "Any time" for a task with only a day. */
function window(task: ScheduleItem) {
  if (!task.scheduled_for) {
    return 'Any time';
  }
  return task.estimated_finish ? `${time(task.scheduled_for)}–${time(task.estimated_finish)}` : time(task.scheduled_for);
}

/**
 * The top of My Tasks: today, like the website's My schedule — what's
 * overdue, the next job as a card (its next tap and Directions), then
 * the rest of today and what's done. `onChanged` reloads after a tap.
 */
export function TodayPlanView({ plan, onChanged }: { plan: TodayPlan; onChanged: () => void }) {
  const theme = useTheme();
  const router = useRouter();
  const [isBusy, setIsBusy] = useState(false);
  const job = plan.next_job;

  const tap = async (action: string) => {
    if (!job) {
      return;
    }
    setIsBusy(true);
    try {
      await sendTaskAction(job.id, action);
      onChanged();
    } catch (err) {
      Alert.alert('Not saved', err instanceof ApiRequestError ? err.message : 'Please try again.');
    } finally {
      setIsBusy(false);
    }
  };

  const row = (task: ScheduleItem, extra?: string) => (
    <TouchableOpacity
      key={task.id}
      style={[styles.row, { borderColor: theme.backgroundSelected }]}
      onPress={() => router.push(`/task/${task.id}`)}
    >
      <ThemedText type="smallBold">{extra ? `${extra} · ` : ''}{window(task)} · {task.task_number}</ThemedText>
      <ThemedText type="small">{task.customer_name} — {task.site_name}</ThemedText>
      <ThemedText type="small" themeColor="textSecondary">
        {ROLE_LABELS[task.my_role] ?? ''} · {task.status_display}
      </ThemedText>
    </TouchableOpacity>
  );

  return (
    <ThemedView style={styles.wrap}>
      <ThemedText type="smallBold">
        Today · {plan.today_count} {plan.today_count === 1 ? 'task' : 'tasks'}
        {plan.overdue.length ? ` · ${plan.overdue.length} overdue` : ''}
      </ThemedText>

      {plan.overdue.length ? (
        <ThemedView style={[styles.section, styles.overdue, { borderColor: theme.danger }]}>
          <ThemedText type="smallBold" themeColor="danger">Overdue</ThemedText>
          {plan.overdue.map((task) => row(
            task, new Date(`${task.scheduled_for ?? `${task.scheduled_date}T00:00:00`}`).toLocaleDateString(
              [], { weekday: 'short', day: 'numeric', month: 'short' },
            ),
          ))}
        </ThemedView>
      ) : null}

      {job ? (
        <ThemedView style={[styles.card, { backgroundColor: theme.backgroundElement, borderColor: theme.text }]}>
          <ThemedText type="small" themeColor="textSecondary">
            {job.status === 'in_progress' ? 'UNDER WAY' : 'NEXT JOB'}
          </ThemedText>
          <ThemedText type="subtitle">{window(job)}</ThemedText>
          <ThemedText type="smallBold">{job.customer_name} — {job.site_name}</ThemedText>
          {job.address ? <ThemedText type="small" themeColor="textSecondary">{job.address}</ThemedText> : null}
          <ThemedText type="small" themeColor="textSecondary">
            {job.task_number} · {ROLE_LABELS[job.my_role] ?? ''} · {job.priority_display} · {job.status_display}
          </ThemedText>
          <ThemedView style={styles.buttons}>
            {job.next_action && ACTION_LABELS[job.next_action] ? (
              <TouchableOpacity
                style={[styles.button, { backgroundColor: theme.primary }]}
                onPress={() => tap(job.next_action!)}
                disabled={isBusy}
              >
                {isBusy ? <ActivityIndicator color={theme.onPrimary} /> : (
                  <ThemedText style={{ color: theme.onPrimary }}>{ACTION_LABELS[job.next_action]}</ThemedText>
                )}
              </TouchableOpacity>
            ) : null}
            <TouchableOpacity
              style={[styles.button, styles.secondary, { borderColor: theme.primary }]}
              onPress={() => Linking.openURL(job.directions_url)}
            >
              <ThemedText themeColor="primary">Directions</ThemedText>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.button, styles.secondary, { borderColor: theme.primary }]}
              onPress={() => router.push(`/task/${job.id}`)}
            >
              <ThemedText themeColor="primary">Open task</ThemedText>
            </TouchableOpacity>
          </ThemedView>
        </ThemedView>
      ) : (
        <ThemedText themeColor="textSecondary">Nothing left to do today.</ThemedText>
      )}

      {plan.later_today.length ? (
        <ThemedView style={styles.section}>
          <ThemedText type="smallBold">Later today</ThemedText>
          {plan.later_today.map((task) => row(task))}
        </ThemedView>
      ) : null}

      {plan.done_today.length ? (
        <ThemedView style={[styles.section, styles.done]}>
          <ThemedText type="smallBold">Done today</ThemedText>
          {plan.done_today.map((task) => row(task))}
        </ThemedView>
      ) : null}
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: Spacing.three, paddingBottom: Spacing.three, backgroundColor: 'transparent' },
  section: { gap: Spacing.two, backgroundColor: 'transparent' },
  overdue: { borderStartWidth: 4, paddingStart: Spacing.two },
  done: { opacity: 0.6 },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one, borderStartWidth: 4 },
  row: { borderBottomWidth: 1, paddingBottom: Spacing.two, gap: 2 },
  buttons: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, marginTop: Spacing.two, backgroundColor: 'transparent' },
  button: { borderRadius: Spacing.two, paddingVertical: Spacing.two, paddingHorizontal: Spacing.three, alignItems: 'center' },
  secondary: { borderWidth: 1 },
});
