import Ionicons from '@expo/vector-icons/Ionicons';
import { StyleSheet } from 'react-native';

import { MediaStrip } from '@/components/customer-request';
import { MACHINE_PHOTO_KINDS, PhotoSlots, type PhotoKind } from '@/components/photo-slots';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { MachineCardData, RequestFile, ReportedMachine } from '@/types';

/**
 * One machine of the task, the same on every screen (tasks.views.task_machine_cards):
 * its name and serial; what the customer reported for it, as words (their
 * photos go in the slots); then serial plate, before, after side by side —
 * all three mandatory before the report is sent. With `onAdd`/`onUse` (the
 * technician, on the task or the report) each slot offers the customer's
 * photo to confirm, or their own; without (approving) a missing one is said
 * in red. Whatever the customer sent that isn't in a slot shows once, at the
 * bottom.
 */
export function MachineCard({ card, reported, onAdd, onUse, isBusy = false, disabled = false }: {
  card: MachineCardData;
  reported?: ReportedMachine;
  onAdd?: (purpose: PhotoKind['purpose']) => void;
  onUse?: (slot: 'serial_plate' | 'before') => void;
  isBusy?: boolean;
  disabled?: boolean;
}) {
  const theme = useTheme();
  const candidates = card.customer_files ?? [];
  const filled = new Set(card.photos.map((photo) => photo.purpose));
  // Shown in a slot only while it's empty and the technician can confirm it; anything else, once, below.
  // A machine not saved yet takes no slot: what the customer sent shows below until it is.
  const inSlots = onAdd && card.registered ? candidates.filter((item) => !filled.has(item.slot)) : [];
  const also: RequestFile[] = [
    ...candidates.filter((item) => !inSlots.includes(item)).map((item) => ({
      key: item.name, url: item.url, kind: item.kind, name: item.name, serial_plate: item.slot === 'serial_plate',
      added_by_staff: Boolean(item.added_by_staff),
    } as RequestFile)),
    // Their other files (a PDF): never a slot.
    ...(reported?.items ?? []).filter((item) => item.kind !== 'image' && item.kind !== 'video'),
  ];
  return (
    <ThemedView style={[styles.card, { borderColor: theme.backgroundSelected }]}>
      <ThemedView style={styles.titleRow}>
        <Ionicons name="barbell-outline" size={18} color={theme.textSecondary} />
        <ThemedText type="smallBold" style={styles.title}>
          {card.name}{card.serial_no ? ` · ${card.serial_no}` : ''}
        </ThemedText>
      </ThemedView>
      {reported ? (
        <ThemedView style={[styles.reported, { borderColor: theme.backgroundSelected }]}>
          <ThemedText type="small" themeColor={reported.urgent ? 'danger' : undefined}>{reported.problem}</ThemedText>
          {reported.description ? <ThemedText type="small" themeColor="textSecondary">{reported.description}</ThemedText> : null}
        </ThemedView>
      ) : null}
      {card.registered ? (
        <PhotoSlots
          kinds={MACHINE_PHOTO_KINDS} photos={card.photos} candidates={inSlots}
          onAdd={onAdd} onUse={onUse} isBusy={isBusy} disabled={disabled}
        />
      ) : (
        <ThemedText type="small" themeColor="textSecondary">
          Not saved yet: save this machine in the report, then add its serial plate, before and after.
        </ThemedText>
      )}
      {also.length ? (
        <ThemedView style={styles.also}>
          <ThemedText type="small" themeColor="textSecondary">Also sent by the customer</ThemedText>
          <MediaStrip items={also} />
        </ThemedView>
      ) : null}
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, gap: Spacing.two },
  titleRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.one, backgroundColor: 'transparent' },
  title: { flexShrink: 1 },
  // The customer's words, set off from the photos below them.
  reported: { gap: 2, paddingBottom: Spacing.two, borderBottomWidth: 1, backgroundColor: 'transparent' },
  also: { gap: 2, backgroundColor: 'transparent' },
});
