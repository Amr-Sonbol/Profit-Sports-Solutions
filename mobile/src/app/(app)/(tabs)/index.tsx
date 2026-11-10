import { useCallback, useState } from 'react';
import { useFocusEffect, useRouter } from 'expo-router';
import { RefreshControl, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { fetchMyToday } from '@/api/endpoints';
import { BellButton } from '@/components/bell-button';
import { TodayPlanView } from '@/components/today-plan';
import { type ScheduleMode, ScheduleView } from '@/components/schedule-view';
import { type PendingReport, sendPendingReports, withOfflineCopy } from '@/offline';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { TodayPlan } from '@/types';

// The schedule only: today (the next job, its tap and Directions) or the
// calendar. Every open task, and a supervisor's "To plan", are on Tasks.
export default function MyScheduleScreen() {
  const router = useRouter();
  const theme = useTheme();
  const [today, setToday] = useState<TodayPlan | null>(null);
  // Today (the next job first) or the calendar: week, month, year.
  const [mode, setMode] = useState<'today' | ScheduleMode>('today');
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isOffline, setIsOffline] = useState(false);
  const [error, setError] = useState('');
  const [pending, setPending] = useState<PendingReport[]>([]);

  // Sends any report filed with no signal first (src/offline.ts), then
  // loads the list — falling back to the last copy when offline.
  const load = useCallback(async () => {
    setPending(await sendPendingReports());
    try {
      const plan = await withOfflineCopy('my-today', fetchMyToday);
      setToday(plan.data);
      setIsOffline(plan.offline);
      setError('');
    } catch {
      setError('Could not load your tasks.');
    }
  }, []);

  // Refetches every time this tab is focused, not just on mount — a task
  // accepted or completed from its detail screen should already be
  // reflected the moment you come back to the list.
  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const onRefresh = async () => {
    setIsRefreshing(true);
    await load();
    setIsRefreshing(false);
  };

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <ThemedView style={styles.headRow}>
          <ThemedText type="title" style={styles.heading}>
            My schedule
          </ThemedText>
          <BellButton />
        </ThemedView>
        {isOffline ? (
          <ThemedText themeColor="danger" type="small" style={styles.notice}>
            No connection — showing your tasks as they were last loaded.
          </ThemedText>
        ) : null}
        {error ? <ThemedText themeColor="danger" style={styles.notice}>{error}</ThemedText> : null}
        {pending.map((report) => (
          <TouchableOpacity
            key={report.taskId}
            style={[styles.pendingCard, { borderColor: report.error ? theme.danger : theme.primary }]}
            onPress={() => router.push(`/report/${report.taskId}`)}
          >
            <ThemedText type="smallBold">
              {report.error ? 'Report not sent — tap to review' : 'Report pending submission'} — {report.taskNumber}
            </ThemedText>
            <ThemedText type="small" themeColor="textSecondary">
              {report.error ?? 'It will be sent automatically when you are back online. Pull down to retry.'}
            </ThemedText>
          </TouchableOpacity>
        ))}
        {mode !== 'today' ? (
          <ScheduleView
            mode={mode}
            onModeChange={setMode}
            onToday={() => setMode('today')}
            taskRoute={(id) => `/task/${id}`}
          />
        ) : (
        <ScrollView
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={isRefreshing} onRefresh={onRefresh} />}
        >
          <ThemedView style={styles.tabs}>
            {(['today', 'week', 'month', 'year'] as const).map((option) => (
              <TouchableOpacity
                key={option}
                style={[
                  styles.tab, { borderColor: theme.backgroundSelected },
                  mode === option && { backgroundColor: theme.primary, borderColor: theme.primary },
                ]}
                onPress={() => setMode(option)}
              >
                <ThemedText type="small" style={mode === option ? { color: theme.onPrimary } : undefined}>
                  {option[0]!.toUpperCase() + option.slice(1)}
                </ThemedText>
              </TouchableOpacity>
            ))}
          </ThemedView>
          {today ? <TodayPlanView plan={today} onChanged={load} /> : null}
        </ScrollView>
        )}
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  notice: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.two },
  pendingCard: {
    borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one,
    marginHorizontal: Spacing.three, marginBottom: Spacing.two,
  },
  container: { flex: 1 },
  safeArea: { flex: 1 },
  heading: { fontSize: 22, paddingHorizontal: Spacing.three, paddingVertical: Spacing.three },
  headRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingEnd: Spacing.three },
  list: { paddingHorizontal: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  tabs: { flexDirection: 'row', gap: Spacing.two, paddingBottom: Spacing.three, backgroundColor: 'transparent' },
  tab: { flex: 1, borderWidth: 1, borderRadius: 999, paddingVertical: Spacing.two, alignItems: 'center' },
});
