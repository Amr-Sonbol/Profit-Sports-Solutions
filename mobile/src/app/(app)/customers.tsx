import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { FlatList, RefreshControl, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchCustomers } from '@/api/endpoints';
import { SearchBox } from '@/components/search-box';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import { useTheme } from '@/hooks/use-theme';
import type { CustomerRow } from '@/types';

/** More → Customers: the website's customer list, searchable by customer or site. */
export default function CustomersScreen() {
  const router = useRouter();
  const theme = useTheme();
  const [rows, setRows] = useState<CustomerRow[] | null>(null);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const query = useDebouncedValue(search.trim());

  const load = useCallback(async () => {
    try {
      setRows(await fetchCustomers(query));
      setError('');
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Could not load customers.');
    }
  }, [query]);

  useFocusEffect(useCallback(() => {
    load();
  }, [load]));

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <TouchableOpacity onPress={() => router.back()} style={styles.back}>
          <ThemedText themeColor="primary">← Back</ThemedText>
        </TouchableOpacity>
        <ThemedText type="title" style={styles.heading}>Customers</ThemedText>
        <SearchBox value={search} onChangeText={setSearch} placeholder="Search by customer or site" />
        {error ? <ThemedText themeColor="danger" style={styles.message}>{error}</ThemedText> : null}
        <FlatList
          data={rows ?? []}
          keyExtractor={(row) => String(row.id)}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => {
            setRefreshing(true);
            await load();
            setRefreshing(false);
          }} />}
          ListEmptyComponent={rows !== null ? (
            <ThemedText themeColor="textSecondary">{query ? 'No customer matches your search.' : 'No customers yet.'}</ThemedText>
          ) : undefined}
          renderItem={({ item }) => (
            <TouchableOpacity
              style={[styles.card, { backgroundColor: theme.backgroundElement }]}
              onPress={() => router.push({ pathname: '/customer/[id]', params: { id: String(item.id) } })}
            >
              <ThemedText type="smallBold">{item.name}</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                {item.is_internal ? 'Our warehouses' : item.segment} · {item.sites.length} {item.sites.length === 1 ? 'site' : 'sites'}
              </ThemedText>
              {item.sites.length ? <ThemedText type="small" numberOfLines={1}>{item.sites.join(' · ')}</ThemedText> : null}
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
  message: { paddingHorizontal: Spacing.three },
  list: { padding: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
});
