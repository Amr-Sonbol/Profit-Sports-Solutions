import { Image } from 'expo-image';
import { ActivityIndicator, Linking, StyleSheet, TouchableOpacity } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { t } from '@/i18n';
import type { CustomerFile, TaskAttachment } from '@/types';

export type PhotoKind = { purpose: 'serial_plate' | 'before' | 'after'; label: string; missing: string };

/** A machine's photos, all mandatory, in the order they're taken: serial plate, before (the fault: photo or video), after. */
export const MACHINE_PHOTO_KINDS: PhotoKind[] = [
  { purpose: 'serial_plate', label: 'Serial plate', missing: 'No serial plate photo' },
  { purpose: 'before', label: 'Before', missing: 'No photo before' },
  { purpose: 'after', label: 'After', missing: 'No photo after' },
];

/** A photo, or a video's ▶ tile; a tap opens it full size or in the phone's player. */
function Tile({ url, isVideo, cacheKey }: { url: string; isVideo: boolean; cacheKey: string }) {
  const theme = useTheme();
  return (
    <TouchableOpacity onPress={() => Linking.openURL(url)}>
      {isVideo ? (
        <ThemedView style={[styles.thumb, styles.video, { backgroundColor: theme.backgroundSelected }]}>
          <ThemedText type="smallBold">▶</ThemedText>
        </ThemedView>
      ) : (
        <Image source={{ uri: url, cacheKey }} cachePolicy="disk" style={styles.thumb} contentFit="cover" />
      )}
    </TouchableOpacity>
  );
}

/**
 * One column per kind, side by side — one decision each. In each: its photos
 * (a customer's one the technician confirmed says so); then, while it's still
 * empty, the customer's photos for it and one "✓ Use this" / "✓ Use these (n)";
 * then the technician's own "+" (the serial plate: one photo, a new one
 * replaces it; before and after: several at once). Read-only (no `onAdd`,
 * approving): a missing one is said, in red. Each photo shows once.
 */
export function PhotoSlots({ kinds, photos, candidates = [], onAdd, onUse, isBusy, disabled }: {
  kinds: PhotoKind[];
  photos: TaskAttachment[];
  candidates?: CustomerFile[];
  onAdd?: (purpose: PhotoKind['purpose']) => void;
  onUse?: (slot: 'serial_plate' | 'before') => void;
  isBusy: boolean;
  disabled: boolean;
}) {
  const theme = useTheme();
  if (isBusy) {
    return <ActivityIndicator />;
  }
  return (
    <ThemedView style={styles.row}>
      {kinds.map((kind) => {
        const shown = photos.filter((photo) => photo.purpose === kind.purpose);
        const offered = onAdd && !shown.length ? candidates.filter((item) => item.slot === kind.purpose) : [];
        return (
          <ThemedView key={kind.purpose} style={styles.column}>
            <ThemedText type="small" themeColor="textSecondary">{kind.label}</ThemedText>
            {shown.map((photo) => (
              <ThemedView key={photo.id} style={styles.item}>
                <Tile url={photo.url} isVideo={photo.media_type === 'video'} cacheKey={`task-photo-${photo.id}`} />
                {photo.source === 'customer' ? (
                  <ThemedText type="small" themeColor="textSecondary">
                    {t('From the customer · confirmed by {name}', { name: photo.uploaded_by_name ?? '' })}
                  </ThemedText>
                ) : null}
              </ThemedView>
            ))}
            {offered.map((item) => (
              <ThemedView key={item.name} style={styles.item}>
                <Tile url={item.url} isVideo={item.kind === 'video'} cacheKey={item.name} />
                <ThemedText type="small" themeColor="textSecondary">
                  {item.added_by_staff ? 'Added by our team' : 'The customer\u2019s'}
                </ThemedText>
              </ThemedView>
            ))}
            {/* One decision for the slot: all the customer's photos for it, or the technician's own. */}
            {offered.length && kind.purpose !== 'after' ? (
              <TouchableOpacity
                style={[styles.add, { borderColor: theme.primary, backgroundColor: theme.primary }]}
                onPress={() => onUse?.(kind.purpose as 'serial_plate' | 'before')} disabled={disabled}
              >
                <ThemedText type="small" style={{ color: theme.onPrimary }}>
                  {offered.length > 1 ? t('✓ Use these ({count})', { count: offered.length }) : '✓ Use this'}
                </ThemedText>
              </TouchableOpacity>
            ) : null}
            {onAdd ? (
              <TouchableOpacity
                style={[styles.add, { borderColor: theme.primary }]} onPress={() => onAdd(kind.purpose)} disabled={disabled}
                accessibilityLabel={kind.label}
              >
                <ThemedText themeColor="primary" type="small">{offered.length ? '+ Take my own' : `+ ${kind.label}`}</ThemedText>
              </TouchableOpacity>
            ) : !shown.length ? (
              <ThemedText type="small" themeColor="danger">{kind.missing}</ThemedText>
            ) : null}
          </ThemedView>
        );
      })}
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', gap: Spacing.two, backgroundColor: 'transparent' },
  column: { flex: 1, gap: Spacing.one, backgroundColor: 'transparent' },
  item: { gap: 2, backgroundColor: 'transparent' },
  thumb: { width: '100%', aspectRatio: 1, borderRadius: Spacing.one },
  video: { alignItems: 'center', justifyContent: 'center' },
  add: { borderWidth: 1, borderRadius: Spacing.two, paddingVertical: Spacing.one, alignItems: 'center' },
});
