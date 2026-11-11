import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { ActivityIndicator, FlatList, RefreshControl, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { type BellItem, fetchBell } from '@/api/endpoints';
import { OfflineNotice } from '@/components/offline-notice';
import { withCountryCopy } from '@/offline';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { locale } from '@/i18n';

const ago = (at: string) => new Date(at).toLocaleString(locale(), { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

/**
 * The bell, as on the website: new requests and tasks, time changes to
 * approve, escalations, team messages, alarms — newest first, alarms marked.
 * A tap opens what it's about, which reads it.
 */
export default function BellScreen() {
  const router = useRouter();
  const theme = useTheme();
  const [items, setItems] = useState<BellItem[] | null>(null);
  const [count, setCount] = useState(0);
  const [error, setError] = useState('');
  // Showing the last copy kept on the phone (no connection).
  const [offline, setOffline] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const copy = await withCountryCopy('bell', fetchBell);
      const bell = copy.data;
      setOffline(copy.offline);
      setItems(bell.items);
      setCount(bell.count);
      setError('');
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Could not load the notifications.');
    }
  }, []);

  useFocusEffect(useCallback(() => {
    load();
  }, [load]));

  const open = (item: BellItem) => {
    if (!item.opens) {
      return;
    }
    if (item.opens.screen === 'website') {
      router.push({ pathname: '/website', params: { path: item.opens.path ?? '/', title: item.text } });
    } else if (item.opens.screen === 'app') {
      router.push((item.opens.path ?? '/') as never);
    } else {
      router.push(`/${item.opens.screen}/${item.opens.id}` as never);
    }
  };

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <TouchableOpacity onPress={() => router.back()} style={styles.back}>
          <ThemedText themeColor="primary">← Back</ThemedText>
        </TouchableOpacity>
        <ThemedText type="title" style={styles.heading}>Notifications</ThemedText>
        {count > (items?.length ?? 0) ? (
          <ThemedText type="small" themeColor="textSecondary" style={styles.note}>
            The newest {items?.length} of {count}.
          </ThemedText>
        ) : null}
        <OfflineNotice offline={offline} />
        {error ? <ThemedText themeColor="danger" style={styles.note}>{error}</ThemedText> : null}
        {items === null && !error ? <ActivityIndicator /> : null}
        <FlatList
          data={items ?? []}
          keyExtractor={(item, index) => `${item.kind}-${index}`}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => {
            setRefreshing(true);
            await load();
            setRefreshing(false);
          }} />}
          ListEmptyComponent={items ? <ThemedText themeColor="textSecondary">No new notifications.</ThemedText> : undefined}
          renderItem={({ item }) => (
            <TouchableOpacity
              style={[styles.card, { backgroundColor: theme.backgroundElement }, item.alarm && { borderColor: theme.danger, borderWidth: 1 }]}
              onPress={() => open(item)} disabled={!item.opens}
            >
              <Ionicons
                name={item.alarm ? 'alert-circle' : item.kind === 'task_message' ? 'chatbubble-outline' : 'notifications-outline'}
                size={20} color={item.alarm ? theme.danger : theme.primary}
              />
              <ThemedView style={styles.cardBody}>
                <ThemedText type="smallBold" themeColor={item.alarm ? 'danger' : undefined}>{item.text}</ThemedText>
                {item.about ? <ThemedText type="small">{item.about}</ThemedText> : null}
                <ThemedText type="small" themeColor="textSecondary">{ago(item.at)}</ThemedText>
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
  back: { paddingHorizontal: Spacing.three, paddingVertical: Spacing.two },
  heading: { fontSize: 22, paddingHorizontal: Spacing.three, paddingBottom: Spacing.two },
  note: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.two },
  list: { paddingHorizontal: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  card: { flexDirection: 'row', gap: Spacing.two, borderRadius: Spacing.two, padding: Spacing.three, alignItems: 'flex-start' },
  cardBody: { flex: 1, gap: 2, backgroundColor: 'transparent' },
});
