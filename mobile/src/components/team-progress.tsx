import { useCallback, useState } from 'react';
import { useFocusEffect, useRouter } from 'expo-router';
import { FlatList, RefreshControl, StyleSheet, TouchableOpacity } from 'react-native';

import { ApiRequestError } from '@/api/client';
import { Avatar } from '@/components/avatar';
import { fetchTeamProgress } from '@/api/endpoints';
import { monthLabel, Sparkline } from '@/components/line-chart';
import { OfflineNotice } from '@/components/offline-notice';
import { withCountryCopy } from '@/offline';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { TeamProgressRow } from '@/types';
import { hm } from '@/hours';
import { t } from '@/i18n';

/**
 * The website's Team progress, on the phone: the field team this person
 * oversees (a manager: supervisors and technicians; a supervisor:
 * technicians), by rank — this month's completed tasks against last
 * month's, hours, certification and a small line of each one's year.
 */
export function TeamProgressList() {
  const theme = useTheme();
  const router = useRouter();
  const [rows, setRows] = useState<TeamProgressRow[] | null>(null);
  const [month, setMonth] = useState('');
  const [error, setError] = useState('');
  // Showing the last copy kept on the phone (no connection).
  const [offline, setOffline] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const copy = await withCountryCopy('team-progress', fetchTeamProgress);
      const data = copy.data;
      setOffline(copy.offline);
      setRows(data.rows);
      setMonth(data.month);
      setError('');
    } catch (err) {
      setError(
        err instanceof ApiRequestError && err.status === 403
          ? 'You do not have permission to view team progress.'
          : 'Could not load team progress.',
      );
    }
  }, []);

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

  if (error) {
    return <ThemedText themeColor="textSecondary" style={styles.message}>{error}</ThemedText>;
  }

  return (
    <FlatList
      data={rows ?? []}
      keyExtractor={(row) => String(row.id)}
      contentContainerStyle={styles.list}
      refreshControl={<RefreshControl refreshing={isRefreshing} onRefresh={onRefresh} />}
      ListHeaderComponent={month ? (
        <>
        <OfflineNotice offline={offline} />
        <ThemedText type="small" themeColor="textSecondary">
          {monthLabel(month, true)} · completed means the work report was submitted
        </ThemedText>
        </>
      ) : undefined}
      ListEmptyComponent={rows !== null ? (
        <ThemedText themeColor="textSecondary">No one on your team yet.</ThemedText>
      ) : undefined}
      renderItem={({ item }) => (
        // Opens their page: details, availability, skills, schedule.
        <TouchableOpacity
          style={[styles.card, { backgroundColor: theme.backgroundElement }]}
          onPress={() => router.push({ pathname: '/people/[id]', params: { id: String(item.id) } })}
        >
          <ThemedView style={styles.top}>
            <Avatar name={item.name} url={item.photo_url} />
            <ThemedView style={styles.who}>
              <ThemedText type="smallBold">{item.rank ? `#${item.rank}  ` : ''}{item.name}</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">{item.role}</ThemedText>
            </ThemedView>
            <Sparkline values={item.months} />
          </ThemedView>
          <ThemedText type="small">
            <ThemedText type="smallBold">{item.completed}</ThemedText> completed
            {item.change > 0 ? ` ▲ ${item.change}` : item.change < 0 ? ` ▼ ${-item.change}` : ''}
            {' · '}{hm(item.hours)} h
            {' · '}{item.is_certified ? 'Certified' : t('{left} of {total} skills to confirm', { left: item.required - item.certified, total: item.required })}
          </ThemedText>
          <ThemedText type="small" themeColor="primary">Open ›</ThemedText>
        </TouchableOpacity>
      )}
    />
  );
}

const styles = StyleSheet.create({
  message: { paddingHorizontal: Spacing.three },
  list: { paddingHorizontal: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  top: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two, backgroundColor: 'transparent' },
  who: { flex: 1, backgroundColor: 'transparent' },
});
