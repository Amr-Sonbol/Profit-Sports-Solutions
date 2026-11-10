import { useCallback, useRef, useState } from 'react';
import { useFocusEffect, useRouter } from 'expo-router';
import { ActivityIndicator, FlatList, RefreshControl, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchTicketsPage } from '@/api/endpoints';
import { BellButton } from '@/components/bell-button';
import { CountryChooser } from '@/components/country-chooser';
import { SearchBox } from '@/components/search-box';
import { OfflineNotice } from '@/components/offline-notice';
import { withCountryCopy } from '@/offline';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import { useTheme } from '@/hooks/use-theme';
import { ago } from '@/task-time';
import type { TicketPage } from '@/types';

/** The website request list's statuses, and Escalated (waiting on someone above). */
const TABS: [string, string][] = [
  ['new', 'New'], ['escalated', 'Escalated'], ['converted', 'Task created'], ['closed', 'Closed'],
  ['dismissed', 'Dismissed'], ['all', 'All'],
];

/**
 * Customer requests in the country — the website's Tickets list (manage_tickets):
 * a tab per status (New and Escalated with how many), a search across them,
 * 30 at a time. A request opens its own screen (ticket/[id]).
 */
export default function TicketsScreen() {
  const theme = useTheme();
  const router = useRouter();
  const [tab, setTab] = useState('new');
  const [rows, setRows] = useState<TicketPage['results'] | null>(null);
  const [counts, setCounts] = useState<TicketPage['counts'] | null>(null);
  const [count, setCount] = useState(0);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  // Showing the last copy kept on the phone (no connection).
  const [offline, setOffline] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [search, setSearch] = useState('');
  const query = useDebouncedValue(search.trim());
  // Only the newest answer counts when tabs or the search change quickly.
  const asked = useRef(0);

  const load = useCallback(async () => {
    const mine = ++asked.current;
    try {
      const copy = query ? { data: await fetchTicketsPage(tab, query, 1), offline: false }
        : await withCountryCopy(`tickets-${tab}`, () => fetchTicketsPage(tab, '', 1));
      const result = copy.data;
      setOffline(copy.offline);
      if (mine !== asked.current) {
        return;
      }
      setRows(result.results);
      setCounts(result.counts);
      setCount(result.count);
      setPage(1);
      setHasMore(result.has_more);
      setError('');
    } catch (err) {
      if (mine === asked.current) {
        setError(err instanceof ApiRequestError && err.status === 403
          ? 'You do not have permission to see customer requests.' : 'Could not load the requests.');
      }
    }
  }, [tab, query]);

  useFocusEffect(useCallback(() => {
    load();
  }, [load]));

  const loadMore = async () => {
    if (!hasMore || loadingMore) {
      return;
    }
    setLoadingMore(true);
    try {
      const result = await fetchTicketsPage(tab, query, page + 1);
      setRows((current) => [...(current ?? []), ...result.results]);
      setPage(result.page);
      setHasMore(result.has_more);
    } catch {
      // Scrolling again tries again.
    } finally {
      setLoadingMore(false);
    }
  };

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <ThemedView style={styles.headRow}>
          <ThemedText type="title" style={styles.heading}>Tickets</ThemedText>
          <BellButton />
        </ThemedView>
        <CountryChooser onChange={load} />
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.tabs} style={styles.tabsBar}>
          {TABS.map(([value, label]) => {
            const on = tab === value;
            const badge = value === 'new' ? counts?.new : value === 'escalated' ? counts?.escalated : undefined;
            return (
              <TouchableOpacity
                key={value} onPress={() => {
                  if (value !== tab) {
                    setRows(null);
                    setTab(value);
                  }
                }}
                style={[styles.tab, { borderColor: theme.backgroundSelected }, on && { backgroundColor: theme.primary, borderColor: theme.primary }]}
              >
                <ThemedText type="small" style={on ? { color: theme.onPrimary } : undefined}>
                  {label}{badge ? ` · ${badge}` : ''}
                </ThemedText>
              </TouchableOpacity>
            );
          })}
        </ScrollView>
        <SearchBox value={search} onChangeText={setSearch} placeholder="Search by request, gym, contact, phone or PAK" />
        <OfflineNotice offline={offline} />
        {error ? <ThemedText themeColor="danger" style={styles.message}>{error}</ThemedText> : null}
        <FlatList
          data={rows ?? []}
          keyExtractor={(item) => String(item.id)}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={isRefreshing} onRefresh={async () => {
            setIsRefreshing(true);
            await load();
            setIsRefreshing(false);
          }} />}
          onEndReached={loadMore}
          onEndReachedThreshold={0.5}
          ListHeaderComponent={rows ? (
            <ThemedText type="small" themeColor="textSecondary">{count} request{count === 1 ? '' : 's'} · newest first</ThemedText>
          ) : <ActivityIndicator />}
          ListFooterComponent={loadingMore ? <ActivityIndicator /> : undefined}
          ListEmptyComponent={rows && !error ? (
            <ThemedText themeColor="textSecondary">{query ? 'No requests match your search.' : 'Nothing here.'}</ThemedText>
          ) : undefined}
          renderItem={({ item }) => (
            <TouchableOpacity
              style={[styles.card, { backgroundColor: theme.backgroundElement }]}
              onPress={() => router.push(`/ticket/${item.id}` as never)}
            >
              <ThemedView style={styles.row}>
                <ThemedText type="smallBold" style={styles.grow}>{item.ticket_number} — {item.company_name}</ThemedText>
                <ThemedText type="small" themeColor={item.escalated ? 'danger' : 'primary'}>
                  {item.escalated ? 'Escalated' : item.status_display}
                </ThemedText>
              </ThemedView>
              {item.site_description ? <ThemedText type="small" themeColor="textSecondary">{item.site_description}</ThemedText> : null}
              <ThemedText numberOfLines={2}>{item.description}</ThemedText>
              <ThemedView style={styles.row}>
                <ThemedText type="small" themeColor="textSecondary" style={styles.grow}>{item.contact_name}</ThemedText>
                <ThemedText type="small" themeColor="textSecondary">Received {ago(item.submitted_at)}</ThemedText>
              </ThemedView>
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
  headRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingEnd: Spacing.three },
  heading: { fontSize: 22, paddingHorizontal: Spacing.three, paddingVertical: Spacing.three },
  tabsBar: { flexGrow: 0 },
  tabs: { gap: Spacing.two, paddingHorizontal: Spacing.three, paddingBottom: Spacing.two },
  tab: { borderWidth: 1, borderRadius: 999, paddingHorizontal: Spacing.three, paddingVertical: Spacing.one },
  message: { paddingHorizontal: Spacing.three },
  list: { paddingHorizontal: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  row: { flexDirection: 'row', justifyContent: 'space-between', gap: Spacing.two, backgroundColor: 'transparent' },
  grow: { flex: 1 },
});
