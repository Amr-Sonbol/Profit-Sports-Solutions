import { useCallback, useState } from 'react';
import { useFocusEffect, useRouter } from 'expo-router';
import { FlatList, RefreshControl, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { fetchMyTasks, fetchMyToday } from '@/api/endpoints';
import { AllTasks } from '@/components/all-tasks';
import { BellButton } from '@/components/bell-button';
import { TaskCard } from '@/components/task-card';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { ToPlan } from '@/components/to-plan';
import { useSession } from '@/context/auth';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { withOfflineCopy } from '@/offline';
import type { PlanItem, TaskListItem } from '@/types';

/**
 * My tasks, apart from the schedule: a supervisor's "To plan" first (no day
 * or no technician yet), then every open task of mine, newest received first.
 * The supervisor's home tab. Whoever sees the country's tasks (a supervisor:
 * he's responsible for them) can switch to All in the country — the website
 * task list's search and filters (components/all-tasks).
 */
export default function TasksScreen() {
  const router = useRouter();
  const theme = useTheme();
  const { me } = useSession();
  const canSeeAll = me?.permissions?.includes('view_tasks') ?? false;
  const [scope, setScope] = useState<'mine' | 'all'>('mine');
  const [tasks, setTasks] = useState<TaskListItem[] | null>(null);
  const [toPlan, setToPlan] = useState<PlanItem[]>([]);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isOffline, setIsOffline] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const [list, plan] = await Promise.all([
        withOfflineCopy('my-tasks', fetchMyTasks),
        withOfflineCopy('my-today', fetchMyToday),
      ]);
      setTasks(list.data);
      setToPlan(plan.data.to_plan ?? []);
      setIsOffline(list.offline || plan.offline);
      setError('');
    } catch {
      setError('Could not load your tasks.');
    }
  }, []);

  useFocusEffect(useCallback(() => {
    load();
  }, [load]));

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <ThemedView style={styles.headRow}>
          <ThemedText type="title" style={styles.heading}>My tasks</ThemedText>
          <BellButton />
        </ThemedView>
        {isOffline ? (
          <ThemedText themeColor="danger" type="small" style={styles.notice}>
            No connection — showing your tasks as they were last loaded.
          </ThemedText>
        ) : null}
        {error ? <ThemedText themeColor="danger" style={styles.notice}>{error}</ThemedText> : null}
        {canSeeAll ? (
          <ThemedView style={styles.scopes}>
            {(['mine', 'all'] as const).map((option) => (
              <TouchableOpacity
                key={option} onPress={() => setScope(option)}
                style={[styles.scope, { borderColor: theme.backgroundSelected },
                  scope === option && { backgroundColor: theme.primary, borderColor: theme.primary }]}
              >
                <ThemedText type="small" style={scope === option ? { color: theme.onPrimary } : undefined}>
                  {option === 'mine' ? 'Mine' : 'All in the country'}
                </ThemedText>
              </TouchableOpacity>
            ))}
          </ThemedView>
        ) : null}
        {scope === 'all' ? <AllTasks /> : (
          <FlatList
            data={tasks ?? []}
            keyExtractor={(item) => String(item.id)}
            contentContainerStyle={styles.list}
            refreshControl={<RefreshControl refreshing={isRefreshing} onRefresh={async () => {
              setIsRefreshing(true);
              await load();
              setIsRefreshing(false);
            }} />}
            ListHeaderComponent={(
              <>
                <ToPlan items={toPlan} />
                <ThemedText type="smallBold">All my open tasks · newest first</ThemedText>
              </>
            )}
            ListEmptyComponent={tasks !== null ? (
              <ThemedText themeColor="textSecondary">You have no open tasks assigned.</ThemedText>
            ) : undefined}
            // My own screen (my steps, my report); "Plan this task" there opens the full one.
            renderItem={({ item }) => <TaskCard item={item} onPress={() => router.push(`/task/${item.id}`)} />}
          />
        )}
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  heading: { fontSize: 22, paddingHorizontal: Spacing.three, paddingVertical: Spacing.three },
  headRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingEnd: Spacing.three },
  notice: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.two },
  list: { paddingHorizontal: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  scopes: { flexDirection: 'row', gap: Spacing.two, paddingHorizontal: Spacing.three, paddingBottom: Spacing.two },
  scope: { flex: 1, borderWidth: 1, borderRadius: 999, paddingVertical: Spacing.two, alignItems: 'center' },
});
