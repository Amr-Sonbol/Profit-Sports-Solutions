import { useCallback, useState } from 'react';
import { useFocusEffect, useRouter } from 'expo-router';
import { FlatList, RefreshControl, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { fetchMyTasks } from '@/api/endpoints';
import { type PendingReport, sendPendingReports, withOfflineCopy } from '@/offline';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { TaskListItem } from '@/types';

export default function MyTasksScreen() {
  const router = useRouter();
  const theme = useTheme();
  const [tasks, setTasks] = useState<TaskListItem[] | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isOffline, setIsOffline] = useState(false);
  const [error, setError] = useState('');
  const [pending, setPending] = useState<PendingReport[]>([]);

  // Sends any report filed with no signal first (src/offline.ts), then
  // loads the list — falling back to the last copy when offline.
  const load = useCallback(async () => {
    setPending(await sendPendingReports());
    try {
      const { data, offline } = await withOfflineCopy('my-tasks', fetchMyTasks);
      setTasks(data);
      setIsOffline(offline);
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
        <ThemedText type="title" style={styles.heading}>
          My Tasks
        </ThemedText>
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
              {report.error ? 'Report not sent — tap to fix' : 'Report waiting to send'} — {report.taskNumber}
            </ThemedText>
            <ThemedText type="small" themeColor="textSecondary">
              {report.error ?? 'It’ll go automatically when you’re back online. Pull down to retry.'}
            </ThemedText>
          </TouchableOpacity>
        ))}
        <FlatList
          data={tasks ?? []}
          keyExtractor={(item) => String(item.id)}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={isRefreshing} onRefresh={onRefresh} />}
          ListEmptyComponent={
            tasks !== null ? <ThemedText themeColor="textSecondary">No open tasks assigned to you.</ThemedText> : null
          }
          renderItem={({ item }) => (
            <TouchableOpacity
              style={[styles.card, { backgroundColor: theme.backgroundElement }]}
              onPress={() => router.push(`/task/${item.id}`)}
            >
              <ThemedText type="smallBold">{item.task_number}</ThemedText>
              <ThemedText>{item.customer_name} — {item.site_name}</ThemedText>
              <ThemedView style={styles.row}>
                <ThemedText themeColor="primary" type="small">
                  {item.status_display}
                </ThemedText>
                <ThemedText themeColor="textSecondary" type="small">
                  {item.priority_display}
                </ThemedText>
              </ThemedView>
            </TouchableOpacity>
          )}
        />
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
  list: { paddingHorizontal: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  row: { flexDirection: 'row', justifyContent: 'space-between', backgroundColor: 'transparent' },
});
