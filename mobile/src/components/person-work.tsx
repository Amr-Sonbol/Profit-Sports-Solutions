import { useState } from 'react';
import { useRouter } from 'expo-router';
import { StyleSheet, TouchableOpacity } from 'react-native';

import { StatusBadge } from '@/components/status-badge';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { Person } from '@/types';
import { locale, t } from '@/i18n';

function day(at: string | null) {
  return at ? new Date(at).toLocaleDateString(locale(), { weekday: 'short', day: 'numeric', month: 'short' }) : 'No date yet';
}

function stars(rating: number) {
  return '★'.repeat(rating) + '☆'.repeat(Math.max(0, 5 - rating));
}

/**
 * A person's work, as on the website's technician page: what's still to do
 * (today, upcoming, overdue), this month, the customers' satisfaction, trips,
 * then their tasks or the reports they filed (with each customer's rating) —
 * each opening its task.
 */
export function PersonWork({ person }: { person: Person }) {
  const theme = useTheme();
  const router = useRouter();
  const [tab, setTab] = useState<'tasks' | 'reports'>('tasks');
  if (!person.workload) {
    return null;  // an older server
  }
  const figure = (value: number | string, label: string, alert = false) => (
    <ThemedView style={[styles.figure, { backgroundColor: theme.backgroundElement }]}>
      <ThemedText type="subtitle" themeColor={alert ? 'danger' : undefined}>{value}</ThemedText>
      <ThemedText type="small" themeColor="textSecondary">{label}</ThemedText>
    </ThemedView>
  );
  const rows = tab === 'tasks' ? person.tasks ?? [] : person.reports ?? [];
  return (
    <ThemedView style={styles.section}>
      <ThemedView style={styles.figures}>
        {figure(person.workload.today, 'Today')}
        {figure(person.workload.upcoming, 'Upcoming')}
        {figure(person.workload.overdue, 'Overdue', person.workload.overdue > 0)}
        {figure(person.visits_this_month ?? 0, 'Visits this month')}
        {person.rating_average !== null && person.rating_average !== undefined
          ? figure(`${person.rating_average} ★`, t('Satisfaction ({count})', { count: person.rating_count ?? 0 })) : null}
      </ThemedView>

      {person.trips?.length ? (
        <ThemedView style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
          <ThemedText type="smallBold">Trips</ThemedText>
          {person.trips.map((trip, index) => (
            <ThemedText key={index} type="small">
              {trip.country}: {day(trip.start)} – {day(trip.end)}{trip.note ? ` · ${trip.note}` : ''}
            </ThemedText>
          ))}
        </ThemedView>
      ) : null}

      <ThemedView style={styles.switcher}>
        {(['tasks', 'reports'] as const).map((option) => (
          <TouchableOpacity
            key={option} onPress={() => setTab(option)}
            style={[styles.switch, { borderColor: theme.backgroundSelected },
              tab === option && { backgroundColor: theme.primary, borderColor: theme.primary }]}
          >
            <ThemedText type="small" style={tab === option ? { color: theme.onPrimary } : undefined}>
              {option === 'tasks' ? 'Tasks' : 'Reports & ratings'}
            </ThemedText>
          </TouchableOpacity>
        ))}
      </ThemedView>
      {rows.length ? rows.map((row) => (
        <TouchableOpacity
          key={`${tab}-${row.id}`} onPress={() => router.push(`/team-task/${row.id}`)}
          style={[styles.card, { backgroundColor: theme.backgroundElement }]}
        >
          <ThemedView style={styles.head}>
            <ThemedText type="smallBold">{row.task_number}</ThemedText>
            <StatusBadge status={row.status} label={row.status_display} />
          </ThemedView>
          <ThemedText type="small">{row.customer} — {row.site}</ThemedText>
          {'my_role' in row ? (
            <ThemedText type="small" themeColor="textSecondary">
              {row.my_role}{row.still_on_it ? '' : ' (no longer on it)'} · {day(row.scheduled_for)}
            </ThemedText>
          ) : (
            <>
              <ThemedText type="small" themeColor="textSecondary">
                Filed {day(row.submitted_at)} · {row.resolved ? 'Resolved' : 'Not resolved'}
              </ThemedText>
              {row.rating ? (
                <ThemedText type="small" style={{ color: '#B45309' }}>
                  {stars(row.rating)}{row.comment ? `  “${row.comment}”` : ''}
                </ThemedText>
              ) : <ThemedText type="small" themeColor="textSecondary">No rating yet</ThemedText>}
            </>
          )}
        </TouchableOpacity>
      )) : (
        <ThemedText type="small" themeColor="textSecondary">{tab === 'tasks' ? 'No tasks yet.' : 'No reports yet.'}</ThemedText>
      )}
      {rows.length === 20 ? (
        <TouchableOpacity onPress={() => router.push({
          pathname: '/website', params: { path: `/tasks/technicians/${person.id}/?tab=${tab}`, title: person.name },
        })}>
          <ThemedText type="small" themeColor="primary">The latest 20 · see all on the website page ›</ThemedText>
        </TouchableOpacity>
      ) : null}
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  section: { gap: Spacing.two, backgroundColor: 'transparent' },
  figures: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  figure: { borderRadius: Spacing.two, padding: Spacing.two, minWidth: 92, flexGrow: 1 },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: 2 },
  head: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', backgroundColor: 'transparent' },
  switcher: { flexDirection: 'row', gap: Spacing.two, marginTop: Spacing.two, backgroundColor: 'transparent' },
  switch: { flex: 1, borderWidth: 1, borderRadius: 999, paddingVertical: Spacing.two, alignItems: 'center' },
});
