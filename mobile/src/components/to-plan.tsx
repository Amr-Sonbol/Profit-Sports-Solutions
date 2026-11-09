import { useRouter } from 'expo-router';
import { StyleSheet, TouchableOpacity } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { PlanItem } from '@/types';

// How long ago it arrived: "just now", "3 h ago", "2 days ago".
function ago(at: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(at).getTime()) / 60000));
  if (minutes < 60) {
    return minutes < 2 ? 'just now' : `${minutes} min ago`;
  }
  const hours = Math.round(minutes / 60);
  if (hours < 24) {
    return `${hours} h ago`;
  }
  const days = Math.round(hours / 24);
  return days === 1 ? 'yesterday' : `${days} days ago`;
}

const MISSING: Record<'date' | 'lead', string> = { date: 'No day yet', lead: 'No technician yet' };

/**
 * A supervisor's tasks waiting for his decision — no day or no technician
 * yet — newest first. A tap opens the task's planning screen (the team view),
 * where he sets the visit time and the lead (himself too) and helpers.
 */
export function ToPlan({ items }: { items: PlanItem[] }) {
  const router = useRouter();
  const theme = useTheme();
  if (!items.length) {
    return null;
  }
  return (
    <ThemedView style={styles.section}>
      <ThemedText type="smallBold">To plan ({items.length})</ThemedText>
      <ThemedText type="small" themeColor="textSecondary">
        Waiting for you: set the day and the technician.
      </ThemedText>
      {items.map((item) => (
        <TouchableOpacity
          key={item.id}
          style={[styles.card, { borderColor: theme.primary, backgroundColor: theme.backgroundElement }]}
          onPress={() => router.push(`/team-task/${item.id}`)}
          accessibilityRole="button"
        >
          <ThemedView style={styles.head}>
            <ThemedText type="smallBold">{item.task_number}</ThemedText>
            <ThemedText type="small" themeColor="textSecondary">{ago(item.received_at)}</ThemedText>
          </ThemedView>
          <ThemedText>{item.customer_name} — {item.site_name}</ThemedText>
          <ThemedView style={styles.chips}>
            {item.missing.map((gap) => (
              <ThemedView key={gap} style={[styles.chip, { borderColor: theme.danger }]}>
                <ThemedText type="small" themeColor="danger">{MISSING[gap]}</ThemedText>
              </ThemedView>
            ))}
            {item.lead_name ? (
              <ThemedText type="small" themeColor="textSecondary">Lead: {item.lead_name}</ThemedText>
            ) : null}
          </ThemedView>
        </TouchableOpacity>
      ))}
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  section: { gap: Spacing.two, marginBottom: Spacing.three, backgroundColor: 'transparent' },
  card: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  head: { flexDirection: 'row', justifyContent: 'space-between', backgroundColor: 'transparent' },
  chips: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: Spacing.two, backgroundColor: 'transparent' },
  chip: { borderWidth: 1, borderRadius: Spacing.three, paddingHorizontal: Spacing.two, paddingVertical: 2 },
});
