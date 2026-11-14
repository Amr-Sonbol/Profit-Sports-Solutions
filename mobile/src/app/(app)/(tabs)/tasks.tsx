import { useCallback, useState } from 'react';
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { FlatList, RefreshControl, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { fetchMyTasks, fetchMyToday, fetchWaiting, type WaitingItem } from '@/api/endpoints';
import { AllTasks } from '@/components/all-tasks';
import { BellButton } from '@/components/bell-button';
import { InternalTasks } from '@/components/internal-tasks';
import { TaskCard } from '@/components/task-card';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { ToPlan } from '@/components/to-plan';
import { WaitingForMe } from '@/components/waiting-for-me';
import { useSession } from '@/context/auth';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { t } from '@/i18n';
import { withOfflineCopy } from '@/offline';
import type { PlanItem, TaskListItem } from '@/types';

/** Done, report sent: waiting for the manager's final approval — nothing left for me to do. */
const WAITING_APPROVAL = 'completed';

/** To do: the ones with a time first, soonest first; then those with none, newest received first (the server's order). */
function toDoOrder(tasks: TaskListItem[]): TaskListItem[] {
  const timed = tasks.filter((task) => task.scheduled_for)
    .sort((a, b) => Date.parse(a.scheduled_for!) - Date.parse(b.scheduled_for!));
  return [...timed, ...tasks.filter((task) => !task.scheduled_for)];
}

/**
 * My tasks, apart from the schedule: a supervisor's "To plan" first (no day
 * or no technician yet), then what I still have to do — timed ones soonest
 * first — and last, smaller, the ones I've done that wait for approval.
 * The supervisor's home tab. Whoever sees the country's tasks (a supervisor:
 * he's responsible for them) can switch to All in the country — the website
 * task list's search and filters (components/all-tasks). Anyone above a
 * technician also has Internal: stock requests, vendor follow-ups and other
 * work inside the company (components/internal-tasks).
 */
export default function TasksScreen() {
  const router = useRouter();
  const theme = useTheme();
  const { me } = useSession();
  const canSeeAll = me?.permissions?.includes('view_tasks') ?? false;
  const canCreate = me?.permissions?.includes('create_tasks') ?? false;
  const hasInternal = !!me?.role && me.role !== 'technician';
  const scopes: Scope[] = [
    // All in the country (field and internal tasks), then Internal, then Mine.
    ...(canSeeAll ? ['all' as const] : []), ...(hasInternal ? ['internal' as const] : []), 'mine',
  ];
  // ?scope=all (the dashboard's "All open tasks") or ?scope=internal: open there, even when already open.
  const params = useLocalSearchParams<{ scope?: string }>();
  const asked = (value?: string): Scope => (value === 'all' || value === 'internal' ? value : 'mine');
  const [scope, setScope] = useState<Scope>(asked(params.scope));
  const [seenScope, setSeenScope] = useState(params.scope);
  if (params.scope !== seenScope) {
    setSeenScope(params.scope);
    if (params.scope === 'all' || params.scope === 'internal') {
      setScope(params.scope);
    }
  }
  const [tasks, setTasks] = useState<TaskListItem[] | null>(null);
  const toDo = toDoOrder((tasks ?? []).filter((task) => task.status !== WAITING_APPROVAL));
  const waitingApproval = (tasks ?? []).filter((task) => task.status === WAITING_APPROVAL);
  const [toPlan, setToPlan] = useState<PlanItem[]>([]);
  const [waiting, setWaiting] = useState<WaitingItem[]>([]);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isOffline, setIsOffline] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const [list, plan, decisions] = await Promise.all([
        withOfflineCopy('my-tasks', fetchMyTasks),
        withOfflineCopy('my-today', fetchMyToday),
        // A technician has none; never let it stop the rest from loading.
        withOfflineCopy('waiting', fetchWaiting).catch(() => ({ data: [] as WaitingItem[], offline: false })),
      ]);
      setTasks(list.data);
      setToPlan(plan.data.to_plan ?? []);
      setWaiting(decisions.data);
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
          <ThemedView style={styles.headActions}>
            {canCreate ? (
              <TouchableOpacity
                style={[styles.newTask, { backgroundColor: theme.primary }]} onPress={() => router.push('/new-task')}
              >
                <ThemedText type="smallBold" style={{ color: theme.onPrimary }}>+ New task</ThemedText>
              </TouchableOpacity>
            ) : null}
            <BellButton />
          </ThemedView>
        </ThemedView>
        {isOffline ? (
          <ThemedText themeColor="danger" type="small" style={styles.notice}>
            No connection — showing your tasks as they were last loaded.
          </ThemedText>
        ) : null}
        {error ? <ThemedText themeColor="danger" style={styles.notice}>{error}</ThemedText> : null}
        {scopes.length > 1 ? (
          <ThemedView style={styles.scopes}>
            {scopes.map((option) => (
              <TouchableOpacity
                key={option} onPress={() => setScope(option)}
                style={[styles.scope, { borderColor: theme.backgroundSelected },
                  scope === option && { backgroundColor: theme.primary, borderColor: theme.primary }]}
              >
                <ThemedText type="small" style={scope === option ? { color: theme.onPrimary } : undefined}>
                  {SCOPE_LABELS[option]}
                </ThemedText>
              </TouchableOpacity>
            ))}
          </ThemedView>
        ) : null}
        {scope === 'all' && canSeeAll ? <AllTasks withInternal={hasInternal} /> : scope === 'internal' && hasInternal ? <InternalTasks /> : (
          <FlatList
            data={toDo}
            keyExtractor={(item) => String(item.id)}
            contentContainerStyle={styles.list}
            refreshControl={<RefreshControl refreshing={isRefreshing} onRefresh={async () => {
              setIsRefreshing(true);
              await load();
              setIsRefreshing(false);
            }} />}
            ListHeaderComponent={(
              <>
                <WaitingForMe items={waiting} />
                <ToPlan items={toPlan} />
                <ThemedText type="smallBold">{t('To do ({count})', { count: toDo.length })}</ThemedText>
              </>
            )}
            ListEmptyComponent={tasks !== null ? (
              <ThemedText themeColor="textSecondary">Nothing to do right now.</ThemedText>
            ) : undefined}
            // Done, waiting for the manager's approval: last, and apart from what's still to do.
            ListFooterComponent={waitingApproval.length ? (
              <ThemedView style={styles.doneGroup}>
                <ThemedText type="smallBold" themeColor="textSecondary">
                  {t('Done, waiting for approval ({count})', { count: waitingApproval.length })}
                </ThemedText>
                {waitingApproval.map((item) => (
                  <TaskCard key={item.id} item={item} onPress={() => router.push(`/task/${item.id}`)} />
                ))}
              </ThemedView>
            ) : undefined}
            // My own screen (my steps, my report); "Plan this task" there opens the full one.
            renderItem={({ item }) => <TaskCard item={item} onPress={() => router.push(`/task/${item.id}`)} />}
          />
        )}
      </SafeAreaView>
    </ThemedView>
  );
}

type Scope = 'mine' | 'all' | 'internal';
const SCOPE_LABELS: Record<Scope, string> = { mine: 'Mine', all: 'All in the country', internal: 'Internal' };

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  heading: { fontSize: 22, paddingHorizontal: Spacing.three, paddingVertical: Spacing.three },
  headRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingEnd: Spacing.three },
  headActions: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two, backgroundColor: 'transparent' },
  newTask: { borderRadius: Spacing.two, paddingHorizontal: Spacing.three, paddingVertical: Spacing.two },
  notice: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.two },
  list: { paddingHorizontal: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  doneGroup: { gap: Spacing.two, marginTop: Spacing.three, backgroundColor: 'transparent' },
  scopes: { flexDirection: 'row', gap: Spacing.two, paddingHorizontal: Spacing.three, paddingBottom: Spacing.two },
  scope: { flex: 1, borderWidth: 1, borderRadius: 999, paddingVertical: Spacing.two, alignItems: 'center' },
});
