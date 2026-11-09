import { StyleSheet, Text, View } from 'react-native';

import { Spacing } from '@/constants/theme';

// One colour per task status, readable on light and dark: a tinted
// background with the same colour, stronger, for the text.
const COLORS: Record<string, string> = {
  new: '#6B7280',
  assigned: '#2563EB',
  accepted: '#0D9488',
  in_progress: '#D97706',
  pending_supervisor_review: '#7C3AED',
  completed: '#16A34A',
  closed: '#15803D',
  blocked: '#DC2626',
  cancelled: '#9CA3AF',
};

/** A task's status as a coloured pill (`status` the code, `label` its words). */
export function StatusBadge({ status, label }: { status: string; label: string }) {
  const color = COLORS[status] ?? COLORS.new;
  return (
    <View style={[styles.badge, { backgroundColor: `${color}22`, borderColor: `${color}66` }]}>
      <Text style={[styles.text, { color }, status === 'cancelled' && styles.cancelled]}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    alignSelf: 'flex-start', borderWidth: 1, borderRadius: 999,
    paddingHorizontal: Spacing.two, paddingVertical: 2,
  },
  text: { fontSize: 12, fontWeight: '600' },
  cancelled: { textDecorationLine: 'line-through' },
});
