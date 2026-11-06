import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchHours } from '@/api/endpoints';
import { monthLabel } from '@/components/line-chart';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { HoursPage } from '@/types';

/**
 * More → Hours: the website's Hours page — for a month, each person you
 * oversee with their reports, time on site (from their taps) and the
 * warnings worth a question; tap a person for the reports behind it.
 * The CSV export for payroll is on the website.
 */
export default function HoursScreen() {
  const router = useRouter();
  const theme = useTheme();
  const [month, setMonth] = useState('');
  const [page, setPage] = useState<HoursPage | null>(null);
  const [error, setError] = useState('');
  const [open, setOpen] = useState<number | null>(null);

  useFocusEffect(useCallback(() => {
    fetchHours(month).then((data) => {
      setPage(data);
      setError('');
    }).catch((err) => {
      setError(err instanceof ApiRequestError ? err.message : 'Could not load the hours.');
    });
  }, [month]));

  const go = (value: string) => {
    setOpen(null);
    setMonth(value.slice(0, 7));
  };

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <ScrollView contentContainerStyle={styles.content}>
          <TouchableOpacity onPress={() => router.back()}>
            <ThemedText themeColor="primary">← Back</ThemedText>
          </TouchableOpacity>
          <ThemedText type="title">Hours</ThemedText>
          {error ? <ThemedText themeColor="danger">{error}</ThemedText> : null}
          {!page ? <ActivityIndicator /> : (
            <>
              <ThemedView style={styles.monthRow}>
                <TouchableOpacity onPress={() => go(page.prev_month)} style={styles.arrow}>
                  <Ionicons name="chevron-back" size={22} color={theme.primary} />
                </TouchableOpacity>
                <ThemedText type="subtitle">{monthLabel(page.month, true)}</ThemedText>
                <TouchableOpacity
                  onPress={() => go(page.next_month)} style={styles.arrow} disabled={page.is_this_month}
                >
                  <Ionicons name="chevron-forward" size={22} color={page.is_this_month ? theme.backgroundSelected : theme.primary} />
                </TouchableOpacity>
              </ThemedView>
              <ThemedText themeColor="textSecondary">
                {page.total_hours} hours across {page.total_reports} {page.total_reports === 1 ? 'report' : 'reports'}
              </ThemedText>

              {page.rows.map((row) => (
                <TouchableOpacity
                  key={row.id}
                  style={[styles.card, { backgroundColor: theme.backgroundElement }]}
                  onPress={() => setOpen(open === row.id ? null : row.id)}
                >
                  <ThemedView style={styles.top}>
                    <ThemedText type="smallBold" style={styles.name}>{row.name}</ThemedText>
                    <ThemedText type="smallBold">{row.hours} h</ThemedText>
                  </ThemedView>
                  <ThemedText type="small" themeColor="textSecondary">
                    {row.reports_count} {row.reports_count === 1 ? 'report' : 'reports'}
                    {row.estimated ? ` · est. ${row.estimated} h` : ''}
                    {row.helped_on ? ` · helped on ${row.helped_on}` : ''}
                  </ThemedText>
                  {row.overrun_count ? (
                    <ThemedText type="small" themeColor="danger">{row.overrun_count} well over the estimate</ThemedText>
                  ) : null}
                  {row.away_count ? (
                    <ThemedText type="small" themeColor="danger">{row.away_count} with taps away from the site</ThemedText>
                  ) : null}
                  {open === row.id ? row.reports.map((report) => (
                    <TouchableOpacity key={report.task_id} onPress={() => router.push(`/team-task/${report.task_id}`)}>
                      <ThemedText type="small" themeColor="primary">
                        {report.task_number} · {report.customer_name} · {report.hours} h ›
                      </ThemedText>
                    </TouchableOpacity>
                  )) : null}
                </TouchableOpacity>
              ))}
            </>
          )}
        </ScrollView>
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  content: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.six, gap: Spacing.two },
  monthRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: 'transparent' },
  arrow: { padding: Spacing.two },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  top: { flexDirection: 'row', justifyContent: 'space-between', gap: Spacing.two, backgroundColor: 'transparent' },
  name: { flex: 1 },
});
