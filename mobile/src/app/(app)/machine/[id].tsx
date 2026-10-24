import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchMachine } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { MachinePage } from '@/types';

/** One machine's history — every visit that recorded it, with what was found and done. */
export default function MachineScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const theme = useTheme();
  const [machine, setMachine] = useState<MachinePage | null>(null);
  const [error, setError] = useState('');

  useFocusEffect(useCallback(() => {
    fetchMachine(Number(id)).then(setMachine).catch((err) => {
      setError(err instanceof ApiRequestError ? err.message : 'Could not load this machine.');
    });
  }, [id]));

  if (!machine) {
    return (
      <ThemedView style={styles.centered}>
        {error ? <ThemedText themeColor="danger">{error}</ThemedText> : <ActivityIndicator />}
      </ThemedView>
    );
  }

  const day = (value: string) => new Date(value).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' });

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <ScrollView contentContainerStyle={styles.content}>
          <TouchableOpacity onPress={() => router.back()}>
            <ThemedText themeColor="primary">← Back</ThemedText>
          </TouchableOpacity>
          <ThemedText type="title">{machine.name}</ThemedText>
          <ThemedText themeColor="textSecondary">
            {machine.serial ? `Serial ${machine.serial} · ` : ''}{machine.status}
          </ThemedText>
          <TouchableOpacity
            onPress={() => router.push({ pathname: '/customer/[id]', params: { id: String(machine.customer_id) } })}
          >
            <ThemedText themeColor="primary">{machine.customer_name} — {machine.site_name} ›</ThemedText>
          </TouchableOpacity>
          {machine.installed_on ? <ThemedText type="small">Installed {day(machine.installed_on)}</ThemedText> : null}
          {machine.warranty_end ? <ThemedText type="small">Warranty until {day(machine.warranty_end)}</ThemedText> : null}

          <ThemedText type="subtitle">History</ThemedText>
          {machine.history.length ? machine.history.map((visit) => (
            <TouchableOpacity
              key={visit.id}
              style={[styles.card, { backgroundColor: theme.backgroundElement }]}
              onPress={() => router.push(`/team-task/${visit.id}`)}
            >
              <ThemedText type="smallBold">{visit.task_number} · {day(visit.reported_at)}</ThemedText>
              {visit.finding ? <ThemedText type="small">Found: {visit.finding}</ThemedText> : null}
              {visit.action ? <ThemedText type="small">Done: {visit.action}</ThemedText> : null}
              <ThemedText type="small" themeColor="textSecondary">
                {visit.outcome || visit.status_display}{visit.task_type_name ? ` · ${visit.task_type_name}` : ''}
              </ThemedText>
            </TouchableOpacity>
          )) : <ThemedText themeColor="textSecondary">No visits recorded yet.</ThemedText>}
        </ScrollView>
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: Spacing.four },
  content: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.six, gap: Spacing.two },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
});
