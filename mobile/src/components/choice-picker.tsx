import Ionicons from '@expo/vector-icons/Ionicons';
import { useState } from 'react';
import { FlatList, Modal, StyleSheet, TextInput, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { Choice } from '@/types';

/**
 * A form field picking one value from a list — a row showing the choice,
 * opening a searchable list (sites, brands, supervisors, ...). `optional`
 * adds a "None" at the top.
 */
export function ChoicePicker({ label, choices, value, onChange, optional = false, error, placeholder }: {
  label: string; choices: Choice[]; value: string; onChange: (value: string) => void;
  optional?: boolean; error?: string; placeholder?: string;
}) {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const selected = choices.find((choice) => choice.value === value);
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const shown = choices.filter((choice) => words.every((word) => choice.label.toLowerCase().includes(word)));

  const pick = (next: string) => {
    onChange(next);
    setOpen(false);
    setQuery('');
  };

  return (
    <ThemedView style={styles.field}>
      <ThemedText type="smallBold">{label}</ThemedText>
      <TouchableOpacity
        style={[styles.row, { borderColor: error ? theme.danger : theme.backgroundSelected }]}
        onPress={() => setOpen(true)}
      >
        <ThemedText style={styles.rowText} themeColor={selected ? 'text' : 'textSecondary'} numberOfLines={2}>
          {selected?.label ?? placeholder ?? 'Choose…'}
        </ThemedText>
        <Ionicons name="chevron-down" size={18} color={theme.textSecondary} />
      </TouchableOpacity>
      {error ? <ThemedText type="small" themeColor="danger">{error}</ThemedText> : null}

      <Modal visible={open} animationType="slide" onRequestClose={() => setOpen(false)}>
        <ThemedView style={styles.modal}>
          <SafeAreaView style={styles.modal}>
            <ThemedView style={styles.modalBar}>
              <ThemedText type="subtitle" style={styles.modalTitle}>{label}</ThemedText>
              <TouchableOpacity onPress={() => setOpen(false)}>
                <ThemedText themeColor="primary">Close</ThemedText>
              </TouchableOpacity>
            </ThemedView>
            {choices.length > 8 ? (
              <TextInput
                style={[styles.search, { color: theme.text, borderColor: theme.backgroundSelected }]}
                value={query} onChangeText={setQuery} placeholder="Search" autoFocus
                placeholderTextColor={theme.textSecondary} autoCorrect={false}
              />
            ) : null}
            <FlatList
              data={optional ? [{ value: '', label: 'None' }, ...shown] : shown}
              keyExtractor={(choice) => choice.value || 'none'}
              keyboardShouldPersistTaps="handled"
              contentContainerStyle={styles.list}
              renderItem={({ item }) => (
                <TouchableOpacity
                  style={[styles.option, { borderColor: theme.backgroundSelected },
                    item.value === value && { backgroundColor: theme.backgroundElement }]}
                  onPress={() => pick(item.value)}
                >
                  <ThemedText style={styles.rowText}>{item.label}</ThemedText>
                  {item.value === value ? <Ionicons name="checkmark" size={18} color={theme.primary} /> : null}
                </TouchableOpacity>
              )}
            />
          </SafeAreaView>
        </ThemedView>
      </Modal>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  field: { gap: Spacing.one, backgroundColor: 'transparent' },
  row: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.two, borderWidth: 1,
    borderRadius: Spacing.two, padding: Spacing.three,
  },
  rowText: { flex: 1 },
  modal: { flex: 1 },
  modalBar: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: Spacing.three, paddingVertical: Spacing.three,
  },
  modalTitle: { flex: 1 },
  search: { marginHorizontal: Spacing.three, borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three },
  list: { padding: Spacing.three, gap: Spacing.one },
  option: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.two,
    borderBottomWidth: StyleSheet.hairlineWidth, paddingVertical: Spacing.three, paddingHorizontal: Spacing.two,
  },
});
