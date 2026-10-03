import { useCallback, useState } from 'react';
import { useFocusEffect } from 'expo-router';
import { FlatList, RefreshControl, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchNewTickets } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { Ticket } from '@/types';

/**
 * New customer tickets still needing a decision — same manage_tickets
 * permission as the web Tickets screen (api/views.py TicketListView).
 * Read-only here: converting a ticket to a task, dismissing it, or
 * assigning it is all web-only for now (no API endpoint for those yet).
 */
export default function TicketsScreen() {
  const theme = useTheme();
  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const [error, setError] = useState('');
  const [isRefreshing, setIsRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setTickets(await fetchNewTickets());
      setError('');
    } catch (err) {
      setError(
        err instanceof ApiRequestError && err.status === 403
          ? "You don't have access to tickets."
          : 'Could not load tickets.',
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

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <ThemedText type="title" style={styles.heading}>
          Tickets
        </ThemedText>
        {error ? (
          <ThemedText themeColor="textSecondary" style={styles.message}>
            {error}
          </ThemedText>
        ) : (
          <FlatList
            data={tickets ?? []}
            keyExtractor={(item) => String(item.id)}
            contentContainerStyle={styles.list}
            refreshControl={<RefreshControl refreshing={isRefreshing} onRefresh={onRefresh} />}
            ListEmptyComponent={
              tickets !== null ? <ThemedText themeColor="textSecondary">No new tickets.</ThemedText> : null
            }
            renderItem={({ item }) => (
              <ThemedView style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
                <ThemedText type="smallBold">{item.company_name}</ThemedText>
                <ThemedText themeColor="textSecondary" type="small">{item.site_description}</ThemedText>
                <ThemedText>{item.description}</ThemedText>
                <ThemedText themeColor="textSecondary" type="small">
                  {item.contact_name} — {item.contact_phone}
                </ThemedText>
              </ThemedView>
            )}
          />
        )}
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  heading: { fontSize: 22, paddingHorizontal: Spacing.three, paddingVertical: Spacing.three },
  message: { paddingHorizontal: Spacing.three },
  list: { paddingHorizontal: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
});
