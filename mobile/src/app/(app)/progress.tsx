import { useCallback, useState } from 'react';
import { useFocusEffect } from 'expo-router';
import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchMyProgress } from '@/api/endpoints';
import { LineChart, monthLabel } from '@/components/line-chart';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { withOfflineCopy } from '@/offline';
import type { MyProgress } from '@/types';

/** YYYY-MM of `first` (YYYY-MM-DD) moved by `step` months. */
function shiftMonth(first: string, step: number) {
  const [year, month] = first.split('-').map(Number);
  const date = new Date(year, month - 1 + step, 1);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
}

/**
 * The website's My progress, on the phone: the year as a line, this month
 * against last, a month's hours and reports (any past month), rank and
 * certification.
 */
export default function ProgressScreen() {
  const theme = useTheme();
  const [progress, setProgress] = useState<MyProgress | null>(null);
  const [month, setMonth] = useState<string | undefined>(undefined);
  const [error, setError] = useState('');
  const [isRefreshing, setIsRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await withOfflineCopy(`my-progress-${month ?? 'now'}`, () => fetchMyProgress(month));
      setProgress(data);
      setError('');
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Could not load your progress.');
    }
  }, [month]);

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

  if (!progress) {
    return (
      <ThemedView style={styles.centered}>
        {error ? <ThemedText themeColor="danger">{error}</ThemedText> : <ActivityIndicator />}
      </ThemedView>
    );
  }

  const { trend, certification } = progress;
  const tile = (value: string | number, label: string) => (
    <ThemedView style={[styles.tile, { backgroundColor: theme.backgroundElement }]}>
      <ThemedText type="subtitle">{value}</ThemedText>
      <ThemedText type="small" themeColor="textSecondary">{label}</ThemedText>
    </ThemedView>
  );

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <ScrollView
          contentContainerStyle={styles.content}
          refreshControl={<RefreshControl refreshing={isRefreshing} onRefresh={onRefresh} />}
        >
          <ThemedText type="title" style={styles.heading}>My progress</ThemedText>

          <ThemedView style={styles.section}>
            <ThemedText type="smallBold">Tasks completed per month</ThemedText>
            <ThemedText>
              <ThemedText type="subtitle">{trend.this_month}</ThemedText> this month
              {trend.change === null ? '' : trend.change > 0 ? `  ▲ ${trend.change} more than last month`
                : trend.change < 0 ? `  ▼ ${-trend.change} fewer than last month` : '  — the same as last month'}
            </ThemedText>
            <LineChart months={trend.months} top={trend.top} />
            <ThemedText type="small" themeColor="textSecondary">
              Completed means the work report was submitted. Tap a month to see its number.
            </ThemedText>
          </ThemedView>

          <ThemedView style={styles.section}>
            <ThemedView style={styles.monthNav}>
              <TouchableOpacity onPress={() => setMonth(shiftMonth(progress.month.first, -1))}>
                <ThemedText themeColor="primary">‹ Previous</ThemedText>
              </TouchableOpacity>
              <ThemedText type="smallBold">{monthLabel(progress.month.first, true)}</ThemedText>
              <TouchableOpacity
                onPress={() => setMonth(shiftMonth(progress.month.first, 1))}
                disabled={progress.month.is_this_month}
                style={progress.month.is_this_month ? styles.disabled : undefined}
              >
                <ThemedText themeColor="primary">Next ›</ThemedText>
              </TouchableOpacity>
            </ThemedView>
            <ThemedView style={styles.tiles}>
              {tile(progress.month.hours, 'Labour hours')}
              {tile(progress.month.reports, 'Reports filed')}
              {progress.month.over_estimate ? tile(progress.month.over_estimate, 'Over estimate') : null}
            </ThemedView>
          </ThemedView>

          <ThemedView style={styles.tiles}>
            {tile(progress.rank ? `#${progress.rank} of ${progress.leaderboard_size}` : '—', 'Ranking in your country')}
            {tile(
              certification.is_certified ? 'Certified' : `${certification.certified}/${certification.required}`,
              'Skills confirmed',
            )}
          </ThemedView>
        </ScrollView>
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  content: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.six, gap: Spacing.four },
  heading: { fontSize: 22, paddingTop: Spacing.three },
  section: { gap: Spacing.two, backgroundColor: 'transparent' },
  monthNav: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', backgroundColor: 'transparent' },
  disabled: { opacity: 0.4 },
  tiles: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  tile: { flexGrow: 1, minWidth: 120, borderRadius: Spacing.two, padding: Spacing.three, gap: 2 },
});
