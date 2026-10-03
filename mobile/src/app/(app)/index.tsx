import { useCallback, useState } from 'react';
import { useFocusEffect, useRouter } from 'expo-router';
import { FlatList, RefreshControl, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { fetchMyTasks } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { TaskListItem } from '@/types';

export default function MyTasksScreen() {
  const router = useRouter();
  const theme = useTheme();
  const [tasks, setTasks] = useState<TaskListItem[] | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const load = useCallback(async () => {
    const data = await fetchMyTasks();
    setTasks(data);
  }, []);

  // Refetches every time this tab is focused, not just on mount — a task
  // accepted or completed from its detail screen should already be
  // reflected the moment you come back to the list.
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
          My Tasks
        </ThemedText>
        <FlatList
          data={tasks ?? []}
          keyExtractor={(item) => String(item.id)}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={isRefreshing} onRefresh={onRefresh} />}
          ListEmptyComponent={
            tasks !== null ? <ThemedText themeColor="textSecondary">No open tasks assigned to you.</ThemedText> : null
          }
          renderItem={({ item }) => (
            <TouchableOpacity
              style={[styles.card, { backgroundColor: theme.backgroundElement }]}
              onPress={() => router.push(`/task/${item.id}`)}
            >
              <ThemedText type="smallBold">{item.task_number}</ThemedText>
              <ThemedText>{item.customer_name} — {item.site_name}</ThemedText>
              <ThemedView style={styles.row}>
                <ThemedText themeColor="primary" type="small">
                  {item.status_display}
                </ThemedText>
                <ThemedText themeColor="textSecondary" type="small">
                  {item.priority_display}
                </ThemedText>
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
  heading: { fontSize: 22, paddingHorizontal: Spacing.three, paddingVertical: Spacing.three },
  list: { paddingHorizontal: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  row: { flexDirection: 'row', justifyContent: 'space-between', backgroundColor: 'transparent' },
});
