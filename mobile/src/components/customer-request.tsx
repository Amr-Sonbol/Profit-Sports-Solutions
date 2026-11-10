import { Image } from 'expo-image';
import { Linking, StyleSheet, TouchableOpacity } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { FromRequest, RequestFile } from '@/types';

// What the customer reported (tasks.customer_report): where and who, then each
// machine with its serial, problem, their words, photos and videos; files not
// tied to a machine; what they sent later. The technician's own task screen and
// the team's (a supervisor's) show the same.
export function CustomerRequest({ request, directionsUrl }: { request: FromRequest; directionsUrl?: string }) {
  const theme = useTheme();
  return (
    <ThemedView style={styles.section}>
      <ThemedText type="smallBold">{"From the customer's request"} · {request.ticket_number}</ThemedText>
      <ThemedText type="small" themeColor="textSecondary">
        {request.machines.length} machine(s) · {request.photo_count} photo(s)
        {request.video_count ? ` · ${request.video_count} video(s)` : ''}
      </ThemedText>
      {/* Where and who: its own card, apart from the machines. */}
      {request.site_address || request.contact_name || request.contact_phone ? (
        <ThemedView style={[styles.card, { borderColor: theme.backgroundSelected }]}>
          {request.site_address ? (
            <TouchableOpacity onPress={() => directionsUrl && Linking.openURL(directionsUrl)}>
              <ThemedText themeColor="primary">{request.site_name} — {request.site_address}</ThemedText>
            </TouchableOpacity>
          ) : null}
          {request.contact_name ? <ThemedText>{request.contact_name}</ThemedText> : null}
          {request.contact_phone ? (
            <TouchableOpacity onPress={() => Linking.openURL(`tel:${request.contact_phone}`)}>
              <ThemedText themeColor="primary">{request.contact_phone}</ThemedText>
            </TouchableOpacity>
          ) : null}
        </ThemedView>
      ) : null}
      {request.machines.length === 0 ? (
        <>
          {request.subject ? <ThemedText type="smallBold">{request.subject}</ThemedText> : null}
          <ThemedText>{request.message}</ThemedText>
        </>
      ) : null}
      {request.machines.map((machine, index) => (
        <ThemedView key={index} style={[styles.card, { borderColor: theme.backgroundSelected }]}>
          <ThemedText type="smallBold">{machine.name}{machine.serial ? ` · ${machine.serial}` : ''}</ThemedText>
          <ThemedText type="small" themeColor={machine.urgent ? 'danger' : undefined}>{machine.problem}</ThemedText>
          {machine.description ? <ThemedText type="small" themeColor="textSecondary">{machine.description}</ThemedText> : null}
          {machine.items.length ? <MediaStrip items={machine.items} /> : (
            <ThemedText type="small" themeColor="textSecondary">No photos sent for this machine.</ThemedText>
          )}
        </ThemedView>
      ))}
      {request.other_files.length ? (
        <>
          <ThemedText type="smallBold" style={styles.subTitle}>Other files</ThemedText>
          <MediaStrip items={request.other_files} />
        </>
      ) : null}
      {request.sent_later.length ? (
        <>
          <ThemedText type="smallBold" style={styles.subTitle}>Sent later by the customer</ThemedText>
          <MediaStrip items={request.sent_later} />
        </>
      ) : null}
    </ThemedView>
  );
}

// Photos (cached on the phone under a key that doesn't change, so they show
// offline), videos and files; a tap opens it full size, or in the phone's player.
export function MediaStrip({ items }: { items: RequestFile[] }) {
  const theme = useTheme();
  return (
    <ThemedView style={styles.mediaRow}>
      {items.map((item) => (
        <TouchableOpacity key={item.key} onPress={() => Linking.openURL(item.url)} style={styles.mediaTile}>
          {item.kind === 'image' ? (
            <Image source={{ uri: item.url, cacheKey: item.key }} cachePolicy="disk" style={styles.mediaImage} contentFit="cover" />
          ) : (
            <ThemedView style={[styles.mediaImage, styles.mediaPlaceholder, { backgroundColor: theme.backgroundSelected }]}>
              <ThemedText type="smallBold">{item.kind === 'video' ? '▶' : 'PDF'}</ThemedText>
            </ThemedView>
          )}
          {item.serial_plate ? <ThemedText type="small" themeColor="textSecondary">Serial plate</ThemedText> : null}
          {item.added_by_staff ? <ThemedText type="small" themeColor="textSecondary">Added by our team</ThemedText> : null}
        </TouchableOpacity>
      ))}
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  section: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.two },
  // One card each: the contact, and every machine with its own photos.
  card: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, gap: 2, backgroundColor: 'transparent' },
  subTitle: { marginTop: Spacing.two },
  mediaRow: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, marginTop: Spacing.one, backgroundColor: 'transparent' },
  mediaTile: { width: 96, gap: 2 },
  mediaImage: { width: 96, height: 96, borderRadius: Spacing.two },
  mediaPlaceholder: { alignItems: 'center', justifyContent: 'center' },
});
