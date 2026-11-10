import Ionicons from '@expo/vector-icons/Ionicons';
import { useCallback, useEffect, useState } from 'react';
import { useFocusEffect, useRouter } from 'expo-router';
import { FlatList, Linking, RefreshControl, StyleSheet, TouchableOpacity } from 'react-native';

import { ApiRequestError } from '@/api/client';
import { Avatar } from '@/components/avatar';
import { fetchTeamToday } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { TeamTodayRow } from '@/types';

/** Each state's words and colour (tasks.views.team_today). */
const STATES: Record<TeamTodayRow['state'], [string, string]> = {
  blocked: ['Blocked', '#B91C1C'],
  working: ['Working', '#15803D'],
  on_site: ['On site', '#0E7490'],
  paused: ['Paused', '#B45309'],
  on_the_way: ['On the way', '#1D4ED8'],
  free: ['Free', '#6B7280'],
  abroad: ['Abroad', '#7C3AED'],
  off: ['Off', '#9CA3AF'],
};

function clock(at: string) {
  return new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/**
 * Team → Today: the field team this person oversees, right now — who's
 * working, on site, on the way, blocked, free, abroad or off; the task it's
 * about and since when, their next job, today's count; Call and their last
 * place. Refreshes every minute while open.
 */
export function TeamTodayList() {
  const theme = useTheme();
  const router = useRouter();
  const [rows, setRows] = useState<TeamTodayRow[] | null>(null);
  const [error, setError] = useState('');
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [focused, setFocused] = useState(false);

  const load = useCallback(async () => {
    try {
      setRows(await fetchTeamToday());
      setError('');
    } catch (err) {
      setError(err instanceof ApiRequestError && err.status === 403
        ? 'You do not have permission to see the team.' : 'Could not load the team. Pull down to try again.');
    }
  }, []);

  useFocusEffect(useCallback(() => {
    load();
    setFocused(true);
    return () => setFocused(false);
  }, [load]));
  useEffect(() => {
    if (!focused) {
      return;
    }
    const timer = setInterval(load, 60000);
    return () => clearInterval(timer);
  }, [focused, load]);

  const counts = (rows ?? []).reduce<Record<string, number>>((all, row) => ({ ...all, [row.state]: (all[row.state] ?? 0) + 1 }), {});

  return (
    <FlatList
      data={rows ?? []}
      keyExtractor={(item) => String(item.id)}
      contentContainerStyle={styles.list}
      refreshControl={<RefreshControl refreshing={isRefreshing} onRefresh={async () => {
        setIsRefreshing(true);
        await load();
        setIsRefreshing(false);
      }} />}
      ListHeaderComponent={rows ? (
        <ThemedText type="small" themeColor="textSecondary">
          {(['working', 'on_site', 'on_the_way', 'blocked', 'free', 'abroad', 'off'] as const)
            .filter((state) => counts[state]).map((state) => `${counts[state]} ${STATES[state][0].toLowerCase()}`).join(' · ')
            || 'No one in your team here.'}
        </ThemedText>
      ) : undefined}
      ListEmptyComponent={error ? <ThemedText themeColor="danger">{error}</ThemedText> : undefined}
      renderItem={({ item }) => {
        const [label, colour] = STATES[item.state];
        return (
          <ThemedView style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
            <TouchableOpacity
              style={styles.top} onPress={() => router.push({ pathname: '/people/[id]', params: { id: String(item.id) } })}
            >
              <Avatar name={item.name} url={item.photo_url} />
              <ThemedView style={styles.grow}>
                <ThemedText type="smallBold">{item.name}</ThemedText>
                <ThemedText type="small" themeColor="textSecondary">
                  {item.role}{item.visiting_from ? ` · visiting from ${item.visiting_from}` : ''}
                  {item.today_total ? ` · today ${item.today_done} of ${item.today_total} done` : ''}
                </ThemedText>
              </ThemedView>
              <ThemedView style={[styles.badge, { backgroundColor: colour }]}>
                <ThemedText type="small" style={styles.badgeText}>{label}</ThemedText>
              </ThemedView>
            </TouchableOpacity>

            {item.state === 'off' ? (
              <ThemedText type="small" themeColor="textSecondary">{item.off_reason || 'Not available'}</ThemedText>
            ) : null}
            {item.abroad ? (
              <ThemedText type="small" themeColor="textSecondary">
                In {item.abroad.country} until {new Date(item.abroad.until).toLocaleDateString([], { day: 'numeric', month: 'short' })}
              </ThemedText>
            ) : null}
            {item.task ? (
              <TouchableOpacity onPress={() => router.push(`/team-task/${item.task!.id}`)}>
                <ThemedText type="small" themeColor="primary">
                  {item.task.task_number} · {item.task.customer} — {item.task.site}{item.since ? ` · since ${clock(item.since)}` : ''} ›
                </ThemedText>
              </TouchableOpacity>
            ) : null}
            {item.next ? (
              <TouchableOpacity onPress={() => router.push(`/team-task/${item.next!.id}`)}>
                <ThemedText type="small" themeColor="textSecondary">
                  Next: {item.next.scheduled_for ? clock(item.next.scheduled_for) : 'today'} · {item.next.customer} — {item.next.site} ›
                </ThemedText>
              </TouchableOpacity>
            ) : item.state === 'free' ? (
              <ThemedText type="small" themeColor="textSecondary">Nothing else planned today</ThemedText>
            ) : null}

            <ThemedView style={styles.actions}>
              {item.phone ? (
                <TouchableOpacity
                  style={[styles.action, { borderColor: theme.backgroundSelected }]}
                  onPress={() => Linking.openURL(`tel:${item.phone}`)}
                >
                  <Ionicons name="call-outline" size={16} color={theme.primary} />
                  <ThemedText type="small" themeColor="primary">Call</ThemedText>
                </TouchableOpacity>
              ) : null}
              {item.location ? (
                <TouchableOpacity
                  style={[styles.action, { borderColor: item.location.away ? theme.danger : theme.backgroundSelected }]}
                  onPress={() => Linking.openURL(`https://www.google.com/maps/search/?api=1&query=${item.location!.lat},${item.location!.lng}`)}
                >
                  <Ionicons name="location-outline" size={16} color={item.location.away ? theme.danger : theme.primary} />
                  <ThemedText type="small" themeColor={item.location.away ? 'danger' : 'primary'}>
                    {item.location.away ? 'Last place — away from the site' : 'Last place'}
                  </ThemedText>
                </TouchableOpacity>
              ) : null}
            </ThemedView>
          </ThemedView>
        );
      }}
    />
  );
}

const styles = StyleSheet.create({
  list: { paddingHorizontal: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  top: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two },
  grow: { flex: 1, backgroundColor: 'transparent' },
  badge: { borderRadius: 999, paddingHorizontal: Spacing.two, paddingVertical: 2 },
  badgeText: { color: '#FFFFFF' },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, marginTop: Spacing.one, backgroundColor: 'transparent' },
  action: {
    flexDirection: 'row', alignItems: 'center', gap: 4, borderWidth: 1, borderRadius: 999,
    paddingHorizontal: Spacing.two, paddingVertical: Spacing.one,
  },
});
