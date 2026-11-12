import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchPartsToPrepare, type PartsToPrepare } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { locale } from '@/i18n';

/**
 * More → Parts to prepare (the website's page): the repairs whose parts are
 * all in and not prepared yet, the soonest visit first — the warehouse
 * manager's list. A tap opens the task, where they're marked prepared.
 */
export default function PartsToPrepareScreen() {
  const router = useRouter();
  const theme = useTheme();
  const [rows, setRows] = useState<PartsToPrepare[] | null>(null);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setRows(await fetchPartsToPrepare());
      setError('');
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Could not load the list.');
    }
  }, []);

  useFocusEffect(useCallback(() => {
    load();
  }, [load]));

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <ScrollView
          contentContainerStyle={styles.content}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => {
            setRefreshing(true);
            await load();
            setRefreshing(false);
          }} />}
        >
          <TouchableOpacity onPress={() => router.back()}>
            <ThemedText themeColor="primary">← Back</ThemedText>
          </TouchableOpacity>
          <ThemedText type="title">Parts to prepare</ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            Repairs whose parts are all in — the soonest visit first. Open one to mark its parts prepared.
          </ThemedText>
          {error ? <ThemedText themeColor="danger">{error}</ThemedText> : null}
          {!rows ? <ActivityIndicator /> : rows.length ? rows.map((row) => (
            <TouchableOpacity
              key={row.task_id} style={[styles.card, { backgroundColor: theme.backgroundElement }]}
              onPress={() => router.push(`/team-task/${row.task_id}`)}
            >
              <ThemedText type="smallBold">{row.task_number} · {row.customer} — {row.site}</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                {row.scheduled_for
                  ? new Date(row.scheduled_for).toLocaleString(locale(), { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
                  : 'No visit date yet'}
              </ThemedText>
              {row.parts.map((part, index) => (
                <ThemedText key={`${part.code}-${index}`} type="small">
                  {part.quantity} × {part.code}{part.description ? ` — ${part.description}` : ''}
                </ThemedText>
              ))}
            </TouchableOpacity>
          )) : <ThemedText themeColor="textSecondary">Nothing to prepare right now.</ThemedText>}
        </ScrollView>
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  content: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.six, gap: Spacing.two },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
});
