import { useCallback, useState } from 'react';
import { useFocusEffect } from 'expo-router';
import { FlatList, RefreshControl, StyleSheet } from 'react-native';

import { ApiRequestError } from '@/api/client';
import { fetchTeamProgress } from '@/api/endpoints';
import { monthLabel, Sparkline } from '@/components/line-chart';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { TeamProgressRow } from '@/types';

/**
 * The website's Team progress, on the phone: the field team this person
 * oversees (a manager: supervisors and technicians; a supervisor:
 * technicians), by rank — this month's completed tasks against last
 * month's, hours, certification and a small line of each one's year.
 */
export function TeamProgressList() {
  const theme = useTheme();
  const [rows, setRows] = useState<TeamProgressRow[] | null>(null);
  const [month, setMonth] = useState('');
  const [error, setError] = useState('');
  const [isRefreshing, setIsRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await fetchTeamProgress();
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
        <ThemedText type="small" themeColor="textSecondary">
          {monthLabel(month, true)} · completed means the work report was submitted
        </ThemedText>
      ) : undefined}
      ListEmptyComponent={rows !== null ? (
        <ThemedText themeColor="textSecondary">No one on your team yet.</ThemedText>
      ) : undefined}
      renderItem={({ item }) => (
        <ThemedView style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
          <ThemedView style={styles.top}>
            <ThemedText type="smallBold">{item.rank ? `#${item.rank}  ` : ''}{item.name}</ThemedText>
            <Sparkline values={item.months} />
          </ThemedView>
          <ThemedText type="small" themeColor="textSecondary">{item.role}</ThemedText>
          <ThemedText type="small">
            <ThemedText type="smallBold">{item.completed}</ThemedText> completed
            {item.change > 0 ? ` ▲ ${item.change}` : item.change < 0 ? ` ▼ ${-item.change}` : ''}
            {' · '}{item.hours} h
            {' · '}{item.is_certified ? 'Certified' : `${item.certified}/${item.required} skills confirmed`}
          </ThemedText>
        </ThemedView>
      )}
    />
  );
}

const styles = StyleSheet.create({
  message: { paddingHorizontal: Spacing.three },
  list: { paddingHorizontal: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  top: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', backgroundColor: 'transparent' },
});
