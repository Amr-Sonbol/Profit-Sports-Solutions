import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { FlatList, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchMachines } from '@/api/endpoints';
import { SearchBox } from '@/components/search-box';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import { useTheme } from '@/hooks/use-theme';
import type { MachineRow } from '@/types';

/** More → Machines: every machine on file, by serial, model, brand, site or customer. */
export default function MachinesScreen() {
  const router = useRouter();
  const theme = useTheme();
  const [rows, setRows] = useState<MachineRow[] | null>(null);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const query = useDebouncedValue(search.trim());

  useFocusEffect(useCallback(() => {
    fetchMachines(query).then((found) => {
      setRows(found);
      setError('');
    }).catch((err) => {
      setError(err instanceof ApiRequestError ? err.message : 'Could not load machines.');
    });
  }, [query]));

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <TouchableOpacity onPress={() => router.back()} style={styles.back}>
          <ThemedText themeColor="primary">← Back</ThemedText>
        </TouchableOpacity>
        <ThemedText type="title" style={styles.heading}>Machines</ThemedText>
        <SearchBox value={search} onChangeText={setSearch} placeholder="Serial, model, brand, site or customer" />
        {error ? <ThemedText themeColor="danger" style={styles.message}>{error}</ThemedText> : null}
        <FlatList
          data={rows ?? []}
          keyExtractor={(row) => String(row.id)}
          contentContainerStyle={styles.list}
          ListEmptyComponent={rows !== null ? (
            <ThemedText themeColor="textSecondary">{query ? 'No machine matches your search.' : 'No machines on file yet.'}</ThemedText>
          ) : undefined}
          renderItem={({ item }) => (
            <TouchableOpacity
              style={[styles.card, { backgroundColor: theme.backgroundElement }]}
              onPress={() => router.push({ pathname: '/machine/[id]', params: { id: String(item.id) } })}
            >
              <ThemedText type="smallBold">{item.name}{item.serial ? ` · ${item.serial}` : ''}</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                {item.customer_name} — {item.site_name} · {item.status}
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
  message: { paddingHorizontal: Spacing.three },
  list: { padding: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
});
