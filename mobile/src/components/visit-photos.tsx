import { Image } from 'expo-image';
import { Linking, StyleSheet, TouchableOpacity } from 'react-native';

import { MachineCard } from '@/components/machine-card';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import type { MachineCardData, ReportedMachine, TaskAttachment } from '@/types';

/**
 * Approving: the task's machine cards as the technician filled them (the
 * same cards as on the task and the report), read-only — serial plate,
 * before, after side by side, a missing one said in red. Photos not of one
 * machine (from before every photo belonged to one) under "Other photos",
 * so none is lost.
 */
export function VisitPhotos({ cards, attachments, reported = [] }: {
  cards: MachineCardData[]; attachments: TaskAttachment[]; reported?: ReportedMachine[];
}) {
  const onCards = new Set(cards.flatMap((card) => card.photos.map((photo) => photo.id)));
  const others = attachments.filter((item) => item.media_type === 'photo' && !onCards.has(item.id));
  if (!cards.length && !others.length) {
    return <ThemedText type="small" themeColor="textSecondary">No photos were added to this visit.</ThemedText>;
  }
  return (
    <ThemedView style={styles.wrap}>
      {cards.map((card, index) => (
        <MachineCard
          key={card.asset_id ?? `new-${index}`} card={card}
          reported={reported.find((machine) => machine.ticket_machine_id && machine.ticket_machine_id === card.ticket_machine_id)}
        />
      ))}
      {others.length ? (
        <ThemedView style={styles.wrap}>
          <ThemedText type="smallBold">Other photos</ThemedText>
          <ThemedView style={styles.row}>
            {others.map((photo) => (
              <TouchableOpacity key={photo.id} onPress={() => Linking.openURL(photo.url)} style={styles.other}>
                <Image source={{ uri: photo.url }} style={styles.thumb} contentFit="cover" />
              </TouchableOpacity>
            ))}
          </ThemedView>
        </ThemedView>
      ) : null}
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: Spacing.two, backgroundColor: 'transparent' },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  other: { width: '30%' },
  thumb: { width: '100%', aspectRatio: 1, borderRadius: Spacing.one },
});
