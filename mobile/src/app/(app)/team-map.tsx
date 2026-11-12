import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { ActivityIndicator, Linking, RefreshControl, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchTeamMap } from '@/api/endpoints';
import { TeamMapView } from '@/components/team-map-view';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { TeamMap } from '@/types';

/**
 * More → Team map: everyone's last tap today on one map (components/team-map-view),
 * then what needs a follow-up today (late arrivals, taps away from the site,
 * jobs past their estimate) and each technician's last tap — "Show on map"
 * opens it in the phone's Maps. Built only from the location at each tap;
 * nobody is tracked in between.
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
          {error ? <ThemedText themeColor="danger">{error}</ThemedText> : null}
          {!map ? <ActivityIndicator /> : (
            <>
              {/* Everyone at once; tap a pin, then its name, for the task. */}
              {map.pins.length ? (
                <>
                  <TeamMapView pins={map.pins} onOpenTask={(taskId) => router.push(`/team-task/${taskId}`)} />
                  <ThemedText type="small" themeColor="textSecondary">
                    Tap a pin to see who it is; tap its name for the task. Red: away from the site.
                  </ThemedText>
                </>
              ) : (
                <ThemedText themeColor="textSecondary">Nobody has tapped yet today.</ThemedText>
              )}
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
              {map.pins.map((pin, index) => (
                <ThemedView key={`${pin.task_id}-${index}`} style={[styles.pin, { backgroundColor: theme.backgroundElement }]}>
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
