import Ionicons from '@expo/vector-icons/Ionicons';
import type { ReactNode } from 'react';
import { Linking, StyleSheet, TouchableOpacity } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { TaskDetail } from '@/types';

type Branch = Pick<
  TaskDetail, 'customer_name' | 'site_name' | 'site_address' | 'site_contact_name' | 'site_contact_phone' | 'directions_url'
>;

/**
 * The branch: the first card on a task's screens (the task, the report,
 * approving), set apart from the machine cards below it — tinted, with a
 * coloured border and a building icon. Who and where: customer, branch,
 * address, the contact to call, directions. No photos: those belong to a
 * machine (MachineCard). `children`: a screen's own extra buttons.
 */
export function BranchCard({ task, children }: { task: Branch; children?: ReactNode }) {
  const theme = useTheme();
  return (
    <ThemedView style={[styles.card, { borderColor: theme.primary, backgroundColor: theme.backgroundElement }]}>
      <ThemedView style={styles.titleRow}>
        <Ionicons name="business-outline" size={20} color={theme.primary} />
        <ThemedView style={styles.titleText}>
          <ThemedText type="smallBold">{task.customer_name}</ThemedText>
          <ThemedText>{task.site_name}</ThemedText>
        </ThemedView>
      </ThemedView>
      {task.site_address ? <ThemedText type="small" themeColor="textSecondary">{task.site_address}</ThemedText> : null}
      {task.site_contact_name ? (
        <ThemedText type="small">{task.site_contact_name}{task.site_contact_phone ? ` · ${task.site_contact_phone}` : ''}</ThemedText>
      ) : null}
      <ThemedView style={styles.buttons}>
        {task.directions_url ? (
          <TouchableOpacity
            style={[styles.button, { borderColor: theme.primary }]} onPress={() => Linking.openURL(task.directions_url!)}
          >
            <Ionicons name="navigate-outline" size={16} color={theme.primary} />
            <ThemedText type="small" themeColor="primary">Directions</ThemedText>
          </TouchableOpacity>
        ) : null}
        {task.site_contact_phone ? (
          <TouchableOpacity
            style={[styles.button, { borderColor: theme.primary }]}
            onPress={() => Linking.openURL(`tel:${task.site_contact_phone}`)}
          >
            <Ionicons name="call-outline" size={16} color={theme.primary} />
            <ThemedText type="small" themeColor="primary" numberOfLines={1}>
              Call {task.site_contact_name || task.site_contact_phone}
            </ThemedText>
          </TouchableOpacity>
        ) : null}
        {children}
      </ThemedView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: 2, borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.two },
  titleRow: { flexDirection: 'row', alignItems: 'flex-start', gap: Spacing.two, backgroundColor: 'transparent' },
  titleText: { flexShrink: 1, gap: 2, backgroundColor: 'transparent' },
  buttons: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  button: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.one, borderWidth: 1, borderRadius: 999,
    paddingHorizontal: Spacing.three, paddingVertical: Spacing.one, maxWidth: '100%',
  },
});
