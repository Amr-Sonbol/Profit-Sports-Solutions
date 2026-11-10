import { useState } from 'react';
import { useRouter } from 'expo-router';
import { StyleSheet, TouchableOpacity } from 'react-native';

import type { WaitingItem } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { ago } from '@/task-time';

/** The red ones first catch the eye: decisions someone else is stuck on. */
const URGENT = ['blocked', 'escalation', 'schedule_change', 'late_report'];
const SHOWN = 5;

/**
 * Waiting for me (tasks.views.waiting_for): reports to review or approve,
 * visit time changes to approve, blocked tasks I own, requests escalated to me,
 * internal tasks for me — oldest first, each opening where it's decided.
 * Tasks → Mine and the Dashboard show the same list.
 */
export function WaitingForMe({ items }: { items: WaitingItem[] }) {
  const router = useRouter();
  const theme = useTheme();
  const [all, setAll] = useState(false);
  if (!items.length) {
    return null;
  }
  const open = (item: WaitingItem) => {
    if (item.opens.screen === 'website') {
      router.push({ pathname: '/website', params: { path: item.opens.path ?? '/', title: item.title } });
    } else {
      router.push(`/${item.opens.screen}/${item.opens.id}` as never);
    }
  };
  const shown = all ? items : items.slice(0, SHOWN);
  return (
    <ThemedView style={styles.section}>
      <ThemedText type="smallBold">Waiting for me ({items.length})</ThemedText>
      <ThemedText type="small" themeColor="textSecondary">Your decision is needed — oldest first.</ThemedText>
      {shown.map((item, index) => {
        const urgent = URGENT.includes(item.kind);
        return (
          <TouchableOpacity
            key={`${item.kind}-${index}`} accessibilityRole="button" onPress={() => open(item)}
            style={[styles.card, { borderColor: urgent ? theme.danger : theme.primary, backgroundColor: theme.backgroundElement }]}
          >
            <ThemedView style={styles.head}>
              <ThemedText type="smallBold" themeColor={urgent ? 'danger' : 'primary'}>{item.title}</ThemedText>
              {item.at ? <ThemedText type="small" themeColor="textSecondary">{ago(item.at)}</ThemedText> : null}
            </ThemedView>
            <ThemedText>{item.text}</ThemedText>
            {item.detail ? <ThemedText type="small" themeColor="textSecondary" numberOfLines={2}>{item.detail}</ThemedText> : null}
          </TouchableOpacity>
        );
      })}
      {items.length > SHOWN ? (
        <TouchableOpacity onPress={() => setAll(!all)}>
          <ThemedText type="small" themeColor="primary">{all ? 'Show fewer' : `Show all ${items.length}`}</ThemedText>
        </TouchableOpacity>
      ) : null}
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  section: { gap: Spacing.two, marginBottom: Spacing.three, backgroundColor: 'transparent' },
  card: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  head: { flexDirection: 'row', justifyContent: 'space-between', gap: Spacing.two, backgroundColor: 'transparent' },
});
