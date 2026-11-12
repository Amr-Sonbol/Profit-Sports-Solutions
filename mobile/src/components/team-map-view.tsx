import { useEffect, useMemo, useRef, useState } from 'react';
import { StyleSheet, TouchableOpacity } from 'react-native';
import MapView, { Marker } from 'react-native-maps';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { t } from '@/i18n';
import type { TeamMap } from '@/types';

type Pin = TeamMap['pins'][number];
type Spot = { key: string; latitude: number; longitude: number; pins: Pin[] };

/** Room around the pins when the map zooms to them (the same on every side, so it reads the same in Arabic). */
const PADDING = { top: 48, right: 48, bottom: 48, left: 48 };

/** Taps within about 10 metres are one place: one pin for everyone there, not pins hidden under each other. */
function spots(pins: Pin[]): Spot[] {
  const byPlace = new Map<string, Spot>();
  for (const pin of pins) {
    const key = `${pin.lat.toFixed(4)},${pin.lng.toFixed(4)}`;
    const spot = byPlace.get(key) ?? { key, latitude: pin.lat, longitude: pin.lng, pins: [] };
    spot.pins.push(pin);
    byPlace.set(key, spot);
  }
  return [...byPlace.values()];
}

/** All of them in view: several zoomed to fit, one with the area around it. */
function fitAll(map: MapView | null, places: Spot[]) {
  if (places.length > 1) {
    map?.fitToCoordinates(places, { edgePadding: PADDING, animated: true });
  } else if (places[0]) {
    map?.animateToRegion({ latitude: places[0].latitude, longitude: places[0].longitude, latitudeDelta: 0.2, longitudeDelta: 0.2 });
  }
}

/**
 * Every technician's last tap today on one map (react-native-maps: Apple Maps
 * on iPhone, Google Maps on Android), zoomed to show them all — again
 * whenever the taps change (a refresh). Several at one place share one pin;
 * tapping its label lists them under the map, each with their task. A pin
 * with anyone away from their site is red. Only where they tapped — nobody
 * is tracked in between.
 */
export function TeamMapView({ pins, area, onOpenTask }: {
  pins: Pin[];
  /** Where it opens with no pin yet (the country's branches). */
  area?: TeamMap['area'];
  onOpenTask: (taskId: number) => void;
}) {
  const theme = useTheme();
  const map = useRef<MapView>(null);
  const ready = useRef(false);
  const places = useMemo(() => spots(pins), [pins]);
  const [chosen, setChosen] = useState<string | null>(null);
  const first = places[0];

  // All of them in view: once the map is there to measure, and again after every refresh.
  useEffect(() => {
    if (ready.current) fitAll(map.current, places);
  }, [places]);

  const group = places.find((place) => place.key === chosen && place.pins.length > 1);

  return (
    <ThemedView style={styles.wrap}>
      <MapView
        ref={map}
        style={styles.map}
        onMapReady={() => {
          ready.current = true;
          fitAll(map.current, places);
        }}
        initialRegion={first
          ? { latitude: first.latitude, longitude: first.longitude, latitudeDelta: 0.2, longitudeDelta: 0.2 }
          : { latitude: area!.latitude, longitude: area!.longitude, latitudeDelta: area!.delta, longitudeDelta: area!.delta }}
      >
        {places.map((place) => {
          const single = place.pins.length === 1 ? place.pins[0] : null;
          const away = place.pins.some((pin) => pin.away);
          return (
            <Marker
              key={place.key}
              coordinate={{ latitude: place.latitude, longitude: place.longitude }}
              title={single ? single.name : t('{count} technicians', { count: place.pins.length })}
              description={single
                ? `${single.detail} · ${single.place}${single.away ? ` · ${t('Away from the site')}` : ''}`
                : `${place.pins.map((pin) => pin.name).join(', ')} · ${place.pins[0].place}`}
              pinColor={away ? theme.danger : theme.primary}
              onCalloutPress={() => (single ? onOpenTask(single.task_id) : setChosen(place.key))}
            />
          );
        })}
      </MapView>
      {/* Everyone at one place, each with their task. */}
      {group ? (
        <ThemedView style={[styles.group, { borderColor: theme.backgroundSelected }]}>
          <ThemedView style={styles.groupHead}>
            <ThemedText type="smallBold">{group.pins[0].place}</ThemedText>
            <TouchableOpacity onPress={() => setChosen(null)} accessibilityLabel={t('Close')} hitSlop={8}>
              <ThemedText themeColor="textSecondary">✕</ThemedText>
            </TouchableOpacity>
          </ThemedView>
          {group.pins.map((pin, index) => (
            <TouchableOpacity key={`${pin.task_id}-${index}`} style={styles.groupRow} onPress={() => onOpenTask(pin.task_id)}>
              <ThemedText type="small" style={styles.groupName}>
                {pin.name} · {pin.detail}{pin.away ? ` · ${t('Away from the site')}` : ''}
              </ThemedText>
              <ThemedText type="small" themeColor="primary">The task ›</ThemedText>
            </TouchableOpacity>
          ))}
        </ThemedView>
      ) : null}
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: Spacing.two, backgroundColor: 'transparent' },
  map: { width: '100%', height: 340, borderRadius: 12 },
  group: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, gap: Spacing.one },
  groupHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', backgroundColor: 'transparent' },
  groupRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two, paddingVertical: Spacing.one },
  groupName: { flex: 1 },
});
