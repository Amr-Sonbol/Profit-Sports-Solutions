import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchDashboard } from '@/api/endpoints';
import { CountryChooser } from '@/components/country-chooser';
import { WaitingForMe } from '@/components/waiting-for-me';
import { OfflineNotice } from '@/components/offline-notice';
import { withCountryCopy } from '@/offline';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { Dashboard } from '@/types';
import { locale } from '@/i18n';

/** Website lists the app has its own screen for. */
const NATIVE: Record<string, string> = {
  '/tasks/tickets/': '/tickets',
  '/tasks/tickets/internal/': '/tasks?scope=internal',
};

/**
 * Dashboard — a manager's home screen (view_dashboard), for the country
 * they chose: what needs their action (each opens the list behind it),
 * today's follow-ups (late arrivals, taps away from the site, jobs past
 * their estimate), the team's figures and the open tasks that need
 * someone first.
 */
export default function DashboardScreen() {
  const router = useRouter();
  const theme = useTheme();
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState('');
  // Showing the last copy kept on the phone (no connection).
  const [offline, setOffline] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const copy = await withCountryCopy('dashboard', fetchDashboard);
      setData(copy.data);
      setOffline(copy.offline);
      setError('');
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Could not load the dashboard.');
    }
  }, []);

  useFocusEffect(useCallback(() => {
    load();
  }, [load]));

  const open = (url: string, title: string) => {
    const path = url.split('?')[0];
    if (NATIVE[path] && !url.includes('?')) {
      router.push(NATIVE[path] as never);
    } else {
      router.push({ pathname: '/website', params: { path: url, title } });
    }
  };

  const figure = (value: number | string, label: string, alert = false) => (
    <ThemedView style={[styles.figure, { backgroundColor: theme.backgroundElement }]}>
      <ThemedText type="subtitle" style={alert ? { color: theme.danger } : undefined}>{value}</ThemedText>
      <ThemedText type="small" themeColor="textSecondary">{label}</ThemedText>
    </ThemedView>
  );

  const when = (task: Dashboard['tasks'][number]) => {
    if (task.scheduled_for) {
      return new Date(task.scheduled_for).toLocaleString(locale(), { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
    }
    if (task.scheduled_date) {
      return new Date(`${task.scheduled_date}T00:00:00`).toLocaleDateString(locale(), { weekday: 'short', day: 'numeric', month: 'short' });
    }
    return 'Not scheduled';
  };

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <ScrollView
          contentContainerStyle={styles.content}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => {
            setRefreshing(true);
            await load();
            setRefreshing(false);
          }} />}
        >
          <ThemedText type="title">Dashboard</ThemedText>
          <CountryChooser onChange={load} inset={false} />
          <OfflineNotice offline={offline} />
          {error ? <ThemedText themeColor="danger">{error}</ThemedText> : null}
          {!data ? <ActivityIndicator /> : (
            <>
              <WaitingForMe items={data.waiting ?? []} />
              {data.company_summary ? (
                <>
                  <ThemedText type="subtitle">Across the company</ThemedText>
                  <ThemedView style={styles.figures}>
                    {figure(data.company_summary.blocked_long, 'Blocked over 2 working hours', data.company_summary.blocked_long > 0)}
                    {figure(data.company_summary.blocked, 'Blocked', data.company_summary.blocked > 0)}
                    {figure(data.company_summary.unscheduled_overdue, 'No day after 24 working hours', data.company_summary.unscheduled_overdue > 0)}
                    {figure(data.company_summary.new_tasks_today, 'New tasks, last 24 hours')}
                    {figure(data.company_summary.reports_to_review, 'Reports to review')}
                    {figure(data.company_summary.reports_to_approve, 'Reports to approve')}
                    {figure(data.company_summary.reports_late, 'Reports waiting over 2 working days', data.company_summary.reports_late > 0)}
                    {figure(data.company_summary.new_requests, 'New customer requests')}
                    {figure(data.company_summary.escalated_requests, 'Escalated requests', data.company_summary.escalated_requests > 0)}
                    {figure(data.company_summary.locked_staff, 'Locked staff logins', data.company_summary.locked_staff > 0)}
                    {figure(data.company_summary.locked_customers, 'Locked customer logins', data.company_summary.locked_customers > 0)}
                  </ThemedView>
                </>
              ) : null}
              <ThemedText type="subtitle">Needs your action</ThemedText>
              {data.actions.length ? data.actions.map((item, index) => (
                <TouchableOpacity
                  key={index}
                  style={[styles.action, { borderColor: item.level === 'critical' ? theme.danger : theme.primary }]}
                  onPress={() => open(item.url, item.label)}
                >
                  <ThemedText style={styles.flex}>{item.label}</ThemedText>
                  {item.count !== null ? (
                    <ThemedText type="subtitle" style={{ color: item.level === 'critical' ? theme.danger : theme.primary }}>
                      {item.count}
                    </ThemedText>
                  ) : null}
                  <Ionicons name="chevron-forward" size={16} color={theme.textSecondary} />
                </TouchableOpacity>
              )) : <ThemedText themeColor="textSecondary">Nothing else needs you.</ThemedText>}

              {data.follow_ups.length ? (
                <>
                  <ThemedText type="subtitle">Today&apos;s follow-ups</ThemedText>
                  {data.follow_ups.map((item, index) => (
                    <TouchableOpacity
                      key={index}
                      style={[styles.action, { borderColor: item.level === 'critical' ? theme.danger : theme.primary }]}
                      onPress={() => router.push(`/team-task/${item.task_id}`)}
                    >
                      <ThemedText type="small" style={styles.flex}>{item.text}</ThemedText>
                      <Ionicons name="chevron-forward" size={16} color={theme.textSecondary} />
                    </TouchableOpacity>
                  ))}
                </>
              ) : null}

              <ThemedText type="subtitle">The team</ThemedText>
              <ThemedView style={styles.figures}>
                {figure(`${data.stats.available_count}/${data.stats.technician_count}`, 'Technicians available')}
                {figure(data.stats.open_task_count, 'Open tasks')}
                {figure(data.stats.emergency_count, 'Emergencies', data.stats.emergency_count > 0)}
                {figure(data.stats.unassigned_count, 'Without a lead', data.stats.unassigned_count > 0)}
                {figure(data.stats.unscheduled_count, 'Not scheduled')}
              </ThemedView>

              <ThemedText type="subtitle">Open tasks — what needs someone first</ThemedText>
              {data.tasks.map((task) => (
                <TouchableOpacity
                  key={task.id}
                  style={[styles.task, { backgroundColor: theme.backgroundElement }]}
                  onPress={() => router.push(`/team-task/${task.id}`)}
                >
                  <ThemedText type="smallBold">
                    {task.task_number} · {task.customer_name}
                    {task.priority === 'emergency' ? '  ⚠' : ''}
                  </ThemedText>
                  <ThemedText type="small" themeColor="textSecondary">{task.site_name} · {task.status_display}</ThemedText>
                  <ThemedText type="small" themeColor={task.lead ? 'text' : 'danger'}>
                    {task.lead || 'No lead yet'} · {when(task)}
                  </ThemedText>
                </TouchableOpacity>
              ))}
              {data.stats.open_task_count > data.tasks.length ? (
                <TouchableOpacity onPress={() => router.push('/tasks?scope=all')}>
                  <ThemedText themeColor="primary">All {data.stats.open_task_count} open tasks ›</ThemedText>
                </TouchableOpacity>
              ) : null}
            </>
          )}
        </ScrollView>
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  flex: { flex: 1 },
  content: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.six, gap: Spacing.two },
  action: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.two,
    borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three,
  },
  figures: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  figure: { flexBasis: '30%', flexGrow: 1, borderRadius: Spacing.two, padding: Spacing.three, gap: 2 },
  task: { borderRadius: Spacing.two, padding: Spacing.three, gap: 2 },
});
