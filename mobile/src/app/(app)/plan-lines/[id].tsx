import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import {
  ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView, StyleSheet, TextInput, TouchableOpacity,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchTaskLines, saveTaskLines } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { PlannedLine } from '@/types';

const COLORS = [['frame', 'Frame'], ['arm', 'Arm'], ['padding', 'Padding'], ['trim', 'Trim']] as const;

function emptyLine(): PlannedLine {
  return { product_code: '', serial_number: '', quantity: '1', frame: 'Black', arm: 'Black', padding: 'Black', trim: 'Black' };
}

/**
 * A task's machines from the delivery note — the website's Edit screen's
 * product lines: product code, serial if already known, quantity and the
 * four colours (black unless custom). On a receiving, loading or
 * installation step "× 4" becomes four lines, one per machine, to check
 * off on site. Locked once the task is done, except for managers.
 */
export default function PlanLinesScreen() {
  const { id, title } = useLocalSearchParams<{ id: string; title?: string }>();
  const router = useRouter();
  const theme = useTheme();
  const [lines, setLines] = useState<PlannedLine[] | null>(null);
  const [locked, setLocked] = useState(false);
  const [perMachine, setPerMachine] = useState(false);
  const [error, setError] = useState('');
  const [isBusy, setIsBusy] = useState(false);

  useFocusEffect(useCallback(() => {
    fetchTaskLines(Number(id)).then((data) => {
      setLines(data.lines.length ? data.lines.map((line) => ({ ...line, quantity: String(line.quantity) })) : [emptyLine()]);
      setLocked(data.locked);
      setPerMachine(data.one_per_machine);
      setError('');
    }).catch((err) => setError(err instanceof ApiRequestError ? err.message : 'Could not load the machines.'));
  }, [id]));

  const update = (index: number, changes: Partial<PlannedLine>) => {
    setLines((current) => (current ?? []).map((line, i) => (i === index ? { ...line, ...changes } : line)));
  };

  const save = async () => {
    setIsBusy(true);
    setError('');
    try {
      const filled = (lines ?? []).filter((line) => line.product_code.trim());
      await saveTaskLines(Number(id), filled);
      router.back();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Not saved. Please try again.');
    } finally {
      setIsBusy(false);
    }
  };

  const inputStyle = [styles.input, { color: theme.text, borderColor: theme.backgroundSelected }];

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.flex}>
          <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
            <TouchableOpacity onPress={() => router.back()}>
              <ThemedText themeColor="primary">← Back</ThemedText>
            </TouchableOpacity>
            <ThemedText type="title">Machines</ThemedText>
            {title ? <ThemedText themeColor="textSecondary">{title}</ThemedText> : null}
            <ThemedText type="small" themeColor="textSecondary">
              {perMachine
                ? 'The machines from the delivery note. A quantity of 4 becomes 4 lines, one per machine. The serial can wait — it is typed or scanned on site. Colours are black unless custom.'
                : 'The items on the delivery note: a product code for each line. Colours are black unless custom.'}
            </ThemedText>
            {locked ? (
              <ThemedText themeColor="danger">This task is done, so only a manager can change its machines.</ThemedText>
            ) : null}
            {error ? <ThemedText themeColor="danger">{error}</ThemedText> : null}
            {lines === null && !error ? <ActivityIndicator /> : null}
            {(lines ?? []).map((line, index) => (
              <ThemedView key={index} style={[styles.card, { borderColor: theme.backgroundSelected }]}>
                <ThemedView style={styles.row}>
                  <TextInput
                    style={[inputStyle, styles.flex]} value={line.product_code} editable={!locked}
                    onChangeText={(value) => update(index, { product_code: value })}
                    placeholder="Product code" placeholderTextColor={theme.textSecondary} autoCapitalize="characters"
                  />
                  <TextInput
                    style={[inputStyle, styles.quantity]} value={String(line.quantity)} editable={!locked}
                    onChangeText={(value) => update(index, { quantity: value })} keyboardType="number-pad"
                    placeholder="Qty" placeholderTextColor={theme.textSecondary}
                  />
                </ThemedView>
                <TextInput
                  style={inputStyle} value={line.serial_number} editable={!locked}
                  onChangeText={(value) => update(index, { serial_number: value })}
                  placeholder="Serial number (if already known)" placeholderTextColor={theme.textSecondary}
                  autoCapitalize="characters"
                />
                <ThemedView style={styles.row}>
                  {COLORS.map(([field, label]) => (
                    <ThemedView key={field} style={styles.color}>
                      <ThemedText type="small" themeColor="textSecondary">{label}</ThemedText>
                      <TextInput
                        style={inputStyle} value={line[field]} editable={!locked}
                        onChangeText={(value) => update(index, { [field]: value })}
                        placeholder="Black" placeholderTextColor={theme.textSecondary}
                      />
                    </ThemedView>
                  ))}
                </ThemedView>
                {!locked ? (
                  <TouchableOpacity onPress={() => setLines((current) => (current ?? []).filter((_, i) => i !== index))}>
                    <ThemedText type="small" themeColor="danger">Remove</ThemedText>
                  </TouchableOpacity>
                ) : null}
              </ThemedView>
            ))}
            {!locked && lines !== null ? (
              <>
                <TouchableOpacity
                  style={[styles.addButton, { borderColor: theme.primary }]}
                  onPress={() => setLines((current) => [...(current ?? []), emptyLine()])}
                >
                  <ThemedText themeColor="primary">+ Another machine</ThemedText>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.saveButton, { backgroundColor: theme.primary }]} onPress={save} disabled={isBusy}
                >
                  {isBusy ? <ActivityIndicator color={theme.onPrimary} /> : (
                    <ThemedText type="smallBold" style={{ color: theme.onPrimary }}>Save the machines</ThemedText>
                  )}
                </TouchableOpacity>
              </>
            ) : null}
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  flex: { flex: 1 },
  content: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.six, gap: Spacing.two },
  card: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.two },
  row: { flexDirection: 'row', gap: Spacing.one, backgroundColor: 'transparent' },
  input: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, fontSize: 15 },
  quantity: { width: 64 },
  color: { flex: 1, gap: 2, backgroundColor: 'transparent' },
  addButton: { borderWidth: 1, borderStyle: 'dashed', borderRadius: Spacing.two, padding: Spacing.three, alignItems: 'center' },
  saveButton: { borderRadius: Spacing.two, padding: Spacing.three, alignItems: 'center' },
});
