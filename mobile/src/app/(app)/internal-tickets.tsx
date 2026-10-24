import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { FlatList, RefreshControl, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchInternalTickets } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { InternalTicket } from '@/types';

const FILTERS = [['open', 'Open'], ['closed', 'Closed'], ['all', 'All']] as const;

/**
 * More → Internal tickets: stock requests, vendor follow-ups and other
 * internal tickets, open ones first. Each opens its website page in the
 * app — notes back and forth, handing it on, closing it.
 */
export default function InternalTicketsScreen() {
  const router = useRouter();
  const theme = useTheme();
  const [status, setStatus] = useState<(typeof FILTERS)[number][0]>('open');
  const [tickets, setTickets] = useState<InternalTicket[] | null>(null);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setTickets(await fetchInternalTickets(status));
      setError('');
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Could not load the tickets.');
    }
  }, [status]);

  useFocusEffect(useCallback(() => {
    load();
  }, [load]));

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <TouchableOpacity onPress={() => router.back()} style={styles.back}>
          <ThemedText themeColor="primary">← Back</ThemedText>
        </TouchableOpacity>
        <ThemedText type="title" style={styles.heading}>Internal tickets</ThemedText>
        <ThemedView style={styles.filters}>
          {FILTERS.map(([value, label]) => {
            const on = status === value;
            return (
              <TouchableOpacity
                key={value}
                style={[styles.filter, { borderColor: on ? theme.primary : theme.backgroundSelected },
                  on && { backgroundColor: theme.primary }]}
                onPress={() => setStatus(value)}
              >
                <ThemedText type="small" style={on ? { color: theme.onPrimary } : undefined}>{label}</ThemedText>
              </TouchableOpacity>
            );
          })}
        </ThemedView>
        {error ? <ThemedText themeColor="danger" style={styles.message}>{error}</ThemedText> : null}
        <FlatList
          data={tickets ?? []}
          keyExtractor={(ticket) => String(ticket.id)}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => {
            setRefreshing(true);
            await load();
            setRefreshing(false);
          }} />}
          ListEmptyComponent={tickets !== null ? <ThemedText themeColor="textSecondary">No tickets here.</ThemedText> : undefined}
          renderItem={({ item }) => (
            <TouchableOpacity
              style={[styles.card, { backgroundColor: theme.backgroundElement }]}
              onPress={() => router.push({
                pathname: '/website', params: { path: `/tasks/tickets/internal/${item.id}/`, title: item.ticket_number },
              })}
            >
              <ThemedText type="smallBold">{item.ticket_number} · {item.kind}</ThemedText>
              {item.subject ? <ThemedText>{item.subject}</ThemedText> : null}
              <ThemedText type="small" numberOfLines={2}>{item.description}</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                {item.status_display}{item.assigned_to ? ` · ${item.assigned_to}` : ''} ·{' '}
                {new Date(item.submitted_at).toLocaleDateString([], { day: 'numeric', month: 'short' })}
              </ThemedText>
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
  back: { paddingHorizontal: Spacing.three, paddingTop: Spacing.two },
  heading: { paddingHorizontal: Spacing.three, paddingVertical: Spacing.two },
  filters: { flexDirection: 'row', gap: Spacing.two, paddingHorizontal: Spacing.three, backgroundColor: 'transparent' },
  filter: { borderWidth: 1, borderRadius: 999, paddingHorizontal: Spacing.three, paddingVertical: Spacing.one },
  message: { paddingHorizontal: Spacing.three },
  list: { padding: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
});
