import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useEffect, useState } from 'react';
import { FlatList, RefreshControl, StyleSheet, TouchableOpacity } from 'react-native';

import { ApiRequestError } from '@/api/client';
import { fetchInternalTickets } from '@/api/endpoints';
import { TextInput } from '@/components/text-field';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { ago } from '@/task-time';
import type { InternalTicket } from '@/types';

const FILTERS = [['open', 'Open'], ['closed', 'Closed'], ['all', 'All']] as const;

/**
 * Tasks → Internal: work one staff member hands another — mine (for me or
 * opened by me), or all of the country's for a manager — open ones first,
 * with a search; "+ New internal task" opens one.
 */
export function InternalTasks() {
  const router = useRouter();
  const theme = useTheme();
  const [status, setStatus] = useState<(typeof FILTERS)[number][0]>('open');
  const [query, setQuery] = useState('');
  const [searched, setSearched] = useState('');
  const [tickets, setTickets] = useState<InternalTicket[] | null>(null);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  // The search runs once typing stops.
  useEffect(() => {
    const timer = setTimeout(() => setSearched(query.trim()), 400);
    return () => clearTimeout(timer);
  }, [query]);

  const load = useCallback(async () => {
    try {
      setTickets(await fetchInternalTickets(status, searched));
      setError('');
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Could not load the internal tasks.');
    }
  }, [status, searched]);

  useFocusEffect(useCallback(() => {
    load();
  }, [load]));

  return (
    <FlatList
      data={tickets ?? []}
      keyExtractor={(ticket) => String(ticket.id)}
      contentContainerStyle={styles.list}
      keyboardShouldPersistTaps="handled"
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => {
        setRefreshing(true);
        await load();
        setRefreshing(false);
      }} />}
      ListHeaderComponent={(
        <ThemedView style={styles.header}>
          <TouchableOpacity
            style={[styles.newButton, { backgroundColor: theme.primary }]} onPress={() => router.push('/new-internal-task')}
          >
            <ThemedText type="smallBold" style={{ color: theme.onPrimary }}>+ New internal task</ThemedText>
          </TouchableOpacity>
          <TextInput
            style={[styles.search, { color: theme.text, borderColor: theme.backgroundSelected }]}
            value={query} onChangeText={setQuery} placeholder="Search number, subject, details…"
            placeholderTextColor={theme.textSecondary} returnKeyType="search"
          />
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
          {error ? <ThemedText themeColor="danger">{error}</ThemedText> : null}
        </ThemedView>
      )}
      ListEmptyComponent={tickets !== null ? <ThemedText themeColor="textSecondary">No internal tasks here.</ThemedText> : undefined}
      renderItem={({ item }) => (
        <TouchableOpacity
          style={[styles.card, { backgroundColor: theme.backgroundElement }]}
          onPress={() => router.push(`/internal-task/${item.id}`)}
        >
          <ThemedText type="smallBold">{item.ticket_number}</ThemedText>
          {item.subject ? <ThemedText>{item.subject}</ThemedText> : null}
          <ThemedText type="small" numberOfLines={2}>{item.description}</ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            {item.status_display}{item.assigned_to ? ` · ${item.assigned_to}` : ''} · {ago(item.submitted_at)}
          </ThemedText>
        </TouchableOpacity>
      )}
    />
  );
}

const styles = StyleSheet.create({
  list: { padding: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  header: { gap: Spacing.two, backgroundColor: 'transparent' },
  newButton: { alignSelf: 'flex-start', borderRadius: Spacing.two, paddingHorizontal: Spacing.three, paddingVertical: Spacing.two },
  search: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, fontSize: 16 },
  filters: { flexDirection: 'row', gap: Spacing.two, backgroundColor: 'transparent' },
  filter: { borderWidth: 1, borderRadius: 999, paddingHorizontal: Spacing.three, paddingVertical: Spacing.one },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
});
