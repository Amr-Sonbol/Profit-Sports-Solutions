import { StyleSheet, TouchableOpacity } from 'react-native';

import { StatusBadge } from '@/components/status-badge';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { ago } from '@/task-time';
import type { TaskListItem } from '@/types';
import { locale, t } from '@/i18n';

// Done: the lead is who did it.
const DONE = ['pending_supervisor_review', 'completed', 'closed'];

/** One task in a list: number and status, customer and branch, new messages, the lead, the visit, when it came in, the priority. */
export function TaskCard({ item, onPress }: { item: TaskListItem; onPress: () => void }) {
  const theme = useTheme();
  return (
    <TouchableOpacity style={[styles.card, { backgroundColor: theme.backgroundElement }]} onPress={onPress}>
      <ThemedView style={styles.row}>
        <ThemedText type="smallBold">{item.task_number}</ThemedText>
        <StatusBadge status={item.status} label={item.status_display} />
      </ThemedView>
      <ThemedText>{item.customer_name} — {item.site_name}</ThemedText>
      {item.unread_messages ? (
        <ThemedView style={[styles.unread, { backgroundColor: theme.primary }]}>
          <ThemedText type="small" style={{ color: theme.onPrimary }}>
            {item.unread_messages === 1 ? '1 new message' : t('{count} new messages', { count: item.unread_messages })}
          </ThemedText>
        </ThemedView>
      ) : null}
      {item.lead_name !== undefined ? (
        item.lead_name ? (
          <ThemedText type="small">{DONE.includes(item.status) ? 'Done by' : 'Lead'}: {item.lead_name}</ThemedText>
        ) : <ThemedText type="small" themeColor="danger">No technician yet</ThemedText>
      ) : null}
      <ThemedView style={styles.row}>
        <ThemedText themeColor="textSecondary" type="small">
          {item.scheduled_for
            ? `${t('Visit:')} ${new Date(item.scheduled_for).toLocaleString(locale(), { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}`
            : 'No date yet'}
        </ThemedText>
        <ThemedText themeColor={item.priority === 'high' ? 'danger' : 'textSecondary'} type="small">
          {item.priority_display}
        </ThemedText>
      </ThemedView>
      {item.reported_at ? <ThemedText themeColor="textSecondary" type="small">Received {ago(item.reported_at)}</ThemedText> : null}
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', backgroundColor: 'transparent' },
  unread: { alignSelf: 'flex-start', borderRadius: 999, paddingHorizontal: Spacing.two, paddingVertical: 2 },
});
