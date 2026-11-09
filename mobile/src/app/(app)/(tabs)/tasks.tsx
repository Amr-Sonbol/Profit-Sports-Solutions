import { useCallback, useState } from 'react';
import { useFocusEffect, useRouter } from 'expo-router';
import { FlatList, RefreshControl, StyleSheet, TextInput, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { fetchMyTasks, fetchMyToday, fetchTeamTasks } from '@/api/endpoints';
import { StatusBadge } from '@/components/status-badge';
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
 * or no technician yet), then every open task of mine, newest received first,
 * each with its status in colour. The supervisor's home tab. Whoever sees the
 * country's tasks (a supervisor: he's responsible for them) can switch to All —
 * every open task in the country, with search; a task opens its full screen.
 */
export default function TasksScreen() {
  const router = useRouter();
  const theme = useTheme();
  const [tasks, setTasks] = useState<TaskListItem[] | null>(null);
  const [toPlan, setToPlan] = useState<PlanItem[]>([]);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isOffline, setIsOffline] = useState(false);
  const [error, setError] = useState('');
  const { me } = useSession();
  const canSeeAll = me?.permissions?.includes('view_tasks') ?? false;
  const [scope, setScope] = useState<'mine' | 'all'>('mine');
  const [allTasks, setAllTasks] = useState<TaskListItem[] | null>(null);
  const [query, setQuery] = useState('');

  const loadAll = useCallback(async (search: string) => {
    try {
      setAllTasks(await fetchTeamTasks(search));
      setError('');
    } catch {
      setError('Could not load the tasks.');
    }
  }, []);

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

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const onRefresh = async () => {
    setIsRefreshing(true);
    await (scope === 'all' ? loadAll(query) : load());
    setIsRefreshing(false);
  };

  const showAll = (next: 'mine' | 'all') => {
    setScope(next);
    if (next === 'all' && allTasks === null) {
      loadAll('');
    }
  };

  const list = scope === 'all' ? allTasks : tasks;

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <ThemedText type="title" style={styles.heading}>My tasks</ThemedText>
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
                key={option}
                style={[
                  styles.scope, { borderColor: theme.backgroundSelected },
                  scope === option && { backgroundColor: theme.primary, borderColor: theme.primary },
                ]}
                onPress={() => showAll(option)}
              >
                <ThemedText type="small" style={scope === option ? { color: theme.onPrimary } : undefined}>
                  {option === 'mine' ? 'Mine' : 'All in the country'}
                </ThemedText>
              </TouchableOpacity>
            ))}
          </ThemedView>
        ) : null}
        <FlatList
          data={list ?? []}
          keyExtractor={(item) => String(item.id)}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={isRefreshing} onRefresh={onRefresh} />}
          ListHeaderComponent={scope === 'all' ? (
            <TextInput
              style={[styles.search, { color: theme.text, borderColor: theme.backgroundSelected }]}
              value={query} onChangeText={setQuery} placeholder="Search: number, customer, branch, serial"
              placeholderTextColor={theme.textSecondary} returnKeyType="search"
              onSubmitEditing={() => loadAll(query.trim())}
            />
          ) : (
            <>
              <ToPlan items={toPlan} />
              <ThemedText type="smallBold">All my open tasks · newest first</ThemedText>
            </>
          )}
          ListEmptyComponent={
            list !== null ? (
              <ThemedText themeColor="textSecondary">
                {scope === 'all' ? 'No open tasks found.' : 'You have no open tasks assigned.'}
              </ThemedText>
            ) : undefined
          }
          renderItem={({ item }) => (
            <TouchableOpacity
              style={[styles.card, { backgroundColor: theme.backgroundElement }]}
              // Mine: my own screen (my steps, my report); All: the task's full screen.
              onPress={() => router.push(scope === 'all' ? `/team-task/${item.id}` : `/task/${item.id}`)}
            >
              <ThemedView style={styles.row}>
                <ThemedText type="smallBold">{item.task_number}</ThemedText>
                <StatusBadge status={item.status} label={item.status_display} />
              </ThemedView>
              <ThemedText>{item.customer_name} — {item.site_name}</ThemedText>
              <ThemedView style={styles.row}>
                <ThemedText themeColor="textSecondary" type="small">
                  {item.scheduled_for
                    ? new Date(item.scheduled_for).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
                    : 'No day yet'}
                </ThemedText>
                <ThemedText themeColor={item.priority === 'high' ? 'danger' : 'textSecondary'} type="small">
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
  container: { flex: 1 },
  safeArea: { flex: 1 },
  heading: { fontSize: 22, paddingHorizontal: Spacing.three, paddingVertical: Spacing.three },
  notice: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.two },
  list: { paddingHorizontal: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', backgroundColor: 'transparent' },
  scopes: { flexDirection: 'row', gap: Spacing.two, paddingHorizontal: Spacing.three, paddingBottom: Spacing.two },
  scope: { flex: 1, borderWidth: 1, borderRadius: 999, paddingVertical: Spacing.two, alignItems: 'center' },
  search: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, marginBottom: Spacing.one },
});
