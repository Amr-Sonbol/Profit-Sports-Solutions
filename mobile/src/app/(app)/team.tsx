import { useCallback, useState } from 'react';
import { useFocusEffect, useRouter } from 'expo-router';
import { FlatList, RefreshControl, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchTeamTasks } from '@/api/endpoints';
import { SearchBox } from '@/components/search-box';
import { TeamProgressList } from '@/components/team-progress';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import { useTheme } from '@/hooks/use-theme';
import type { TaskListItem } from '@/types';
import { useSession } from '@/context/auth';

/**
 * Every open task in the viewer's active country — same view_tasks
 * permission as the web Tasks list (api/views.py TaskListView). A plain
 * technician doesn't have that permission, so this shows a plain message
 * instead of a list rather than being hidden as a tab entirely (see
 * (app)/_layout.tsx).
 */
export default function TeamTasksScreen() {
  const theme = useTheme();
  const router = useRouter();
  const { me } = useSession();
  const canCreate = me?.permissions?.includes('create_tasks') ?? false;
  const [tasks, setTasks] = useState<TaskListItem[] | null>(null);
  const [error, setError] = useState('');
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [search, setSearch] = useState('');
  const query = useDebouncedValue(search.trim());
  // The team's open tasks, or how each person on it is doing.
  const [view, setView] = useState<'tasks' | 'progress'>('tasks');

  const load = useCallback(async () => {
    try {
      setTasks(await fetchTeamTasks(query));
      setError('');
    } catch (err) {
      setError(
        err instanceof ApiRequestError && err.status === 403
          ? "You do not have permission to view the team task list."
          : 'Could not load tasks.',
      );
    }
  }, [query]);

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
        <ThemedView style={styles.headerRow}>
          <ThemedText type="title" style={styles.heading}>
            Team
          </ThemedText>
          {canCreate ? (
            <TouchableOpacity
              style={[styles.newTask, { backgroundColor: theme.primary }]} onPress={() => router.push('/new-task')}
            >
              <ThemedText type="smallBold" style={{ color: theme.onPrimary }}>+ New task</ThemedText>
            </TouchableOpacity>
          ) : null}
        </ThemedView>
        <ThemedView style={styles.switcher}>
          {(['tasks', 'progress'] as const).map((option) => (
            <TouchableOpacity
              key={option}
              style={[
                styles.switch, { borderColor: theme.backgroundSelected },
                view === option && { backgroundColor: theme.primary, borderColor: theme.primary },
              ]}
              onPress={() => setView(option)}
            >
              <ThemedText style={view === option ? { color: theme.onPrimary } : undefined}>
                {option === 'tasks' ? 'Tasks' : 'Progress'}
              </ThemedText>
            </TouchableOpacity>
          ))}
        </ThemedView>
        {view === 'progress' ? <TeamProgressList /> : (
        <>
        <SearchBox value={search} onChangeText={setSearch} placeholder="Search by task, customer, site, PAK or serial number" />
        {error ? (
          <ThemedText themeColor="textSecondary" style={styles.message}>
            {error}
          </ThemedText>
        ) : (
          <FlatList
            data={tasks ?? []}
            keyExtractor={(item) => String(item.id)}
            contentContainerStyle={styles.list}
            refreshControl={<RefreshControl refreshing={isRefreshing} onRefresh={onRefresh} />}
            ListEmptyComponent={
              tasks !== null ? (
                <ThemedText themeColor="textSecondary">{query ? 'No tasks match your search.' : 'No open tasks.'}</ThemedText>
              ) : undefined
            }
            renderItem={({ item }) => (
              <TouchableOpacity
                style={[styles.card, { backgroundColor: theme.backgroundElement }]}
                onPress={() => router.push(`/team-task/${item.id}`)}
              >
                <ThemedText type="smallBold">{item.task_number}</ThemedText>
                <ThemedText>{item.customer_name} — {item.site_name}</ThemedText>
                <ThemedView style={styles.row}>
                  <ThemedText themeColor="primary" type="small">{item.status_display}</ThemedText>
                  <ThemedText themeColor="textSecondary" type="small">{item.priority_display}</ThemedText>
                </ThemedView>
              </TouchableOpacity>
            )}
          />
        )}
        </>
        )}
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  headerRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingEnd: Spacing.three, backgroundColor: 'transparent',
  },
  newTask: { borderRadius: Spacing.two, paddingHorizontal: Spacing.three, paddingVertical: Spacing.two },
  container: { flex: 1 },
  safeArea: { flex: 1 },
  heading: { fontSize: 22, paddingHorizontal: Spacing.three, paddingVertical: Spacing.three },
  message: { paddingHorizontal: Spacing.three },
  list: { paddingHorizontal: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  row: { flexDirection: 'row', justifyContent: 'space-between', backgroundColor: 'transparent' },
  switcher: {
    flexDirection: 'row', gap: Spacing.two, paddingHorizontal: Spacing.three, paddingBottom: Spacing.two,
    backgroundColor: 'transparent',
  },
  switch: { flex: 1, borderWidth: 1, borderRadius: Spacing.two, paddingVertical: Spacing.two, alignItems: 'center' },
});
