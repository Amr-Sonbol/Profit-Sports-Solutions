import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { ActivityIndicator, Linking, RefreshControl, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchTeamMap } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { TeamMap } from '@/types';

/**
 * More → Team map: what needs a follow-up today (late arrivals, taps away
 * from the site, jobs past their estimate) and where each technician last
 * tapped — "Show on map" opens it in Maps; "All on one map" is the
 * website's map. Built only from the location at each tap; nobody is
 * tracked in between.
 */
export default function TeamMapScreen() {
  const router = useRouter();
  const theme = useTheme();
  const [map, setMap] = useState<TeamMap | null>(null);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setMap(await fetchTeamMap());
      setError('');
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Could not load the team map.');
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
          <ThemedText type="title">Team map</ThemedText>
          <TouchableOpacity
            style={[styles.button, { borderColor: theme.primary }]}
            onPress={() => router.push({ pathname: '/website', params: { path: '/tasks/technicians/map/', title: 'Team map' } })}
          >
            <Ionicons name="map-outline" size={16} color={theme.primary} />
            <ThemedText type="small" themeColor="primary">All on one map</ThemedText>
          </TouchableOpacity>
          {error ? <ThemedText themeColor="danger">{error}</ThemedText> : null}
          {!map ? <ActivityIndicator /> : (
            <>
              <ThemedText type="subtitle">Needs attention</ThemedText>
              {map.attention.length ? map.attention.map((item, index) => (
                <TouchableOpacity
                  key={index}
                  style={[styles.card, { borderColor: item.level === 'critical' ? theme.danger : theme.primary }]}
                  onPress={() => router.push(`/team-task/${item.task_id}`)}
                >
                  <ThemedText type="small">{item.text}</ThemedText>
                </TouchableOpacity>
              )) : <ThemedText themeColor="textSecondary">Nothing right now.</ThemedText>}

              <ThemedText type="subtitle">Last tap today</ThemedText>
              {map.pins.map((pin) => (
                <ThemedView key={pin.name} style={[styles.pin, { backgroundColor: theme.backgroundElement }]}>
                  <ThemedText type="smallBold">{pin.name}</ThemedText>
                  <ThemedText type="small">{pin.detail} · {pin.place}</ThemedText>
                  {pin.away ? <ThemedText type="small" themeColor="danger">Away from the site</ThemedText> : null}
                  <ThemedView style={styles.row}>
                    <TouchableOpacity
                      style={[styles.button, { borderColor: theme.primary }]}
                      onPress={() => Linking.openURL(`https://www.google.com/maps/search/?api=1&query=${pin.lat},${pin.lng}`)}
                    >
                      <Ionicons name="location-outline" size={16} color={theme.primary} />
                      <ThemedText type="small" themeColor="primary">Show on map</ThemedText>
                    </TouchableOpacity>
                    <TouchableOpacity
                      style={[styles.button, { borderColor: theme.primary }]}
                      onPress={() => router.push(`/team-task/${pin.task_id}`)}
                    >
                      <ThemedText type="small" themeColor="primary">The task ›</ThemedText>
                    </TouchableOpacity>
                  </ThemedView>
                </ThemedView>
              ))}
              {map.without_taps.length ? (
                <ThemedText type="small" themeColor="textSecondary">
                  No taps today: {map.without_taps.join(', ')}
                </ThemedText>
              ) : null}
            </>
          )}
        </ScrollView>
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  content: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.six, gap: Spacing.two },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  button: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.one, alignSelf: 'flex-start',
    borderWidth: 1, borderRadius: Spacing.two, paddingHorizontal: Spacing.three, paddingVertical: Spacing.one,
  },
  card: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three },
  pin: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
});
