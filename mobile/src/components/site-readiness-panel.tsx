import { useState } from 'react';
import { StyleSheet, Switch, TouchableOpacity } from 'react-native';

import { ApiRequestError } from '@/api/client';
import { saveSiteReadiness } from '@/api/endpoints';
import { TextInput } from '@/components/text-field';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { SiteReadiness } from '@/types';

const CHECKS = [
  ['space_and_floor', 'Space and floor ready'],
  ['power', 'Power sockets where the machines go'],
  ['access', 'Access for the delivery (lift, doors, parking)'],
  ['date_confirmed', 'The customer confirmed the date'],
] as const;

/** An installation's site checked ready before the truck goes (as on the website): its owner or a manager. */
export function SiteReadinessPanel({ taskId, readiness, onChanged }: {
  taskId: number; readiness: SiteReadiness; onChanged: () => void;
}) {
  const theme = useTheme();
  const [checks, setChecks] = useState(readiness);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState('');

  const save = async () => {
    setIsBusy(true);
    setError('');
    try {
      await saveSiteReadiness(taskId, {
        space_and_floor: checks.space_and_floor, power: checks.power, access: checks.access,
        date_confirmed: checks.date_confirmed, note: checks.note,
      });
      onChanged();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Not saved. Please try again.');
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <ThemedView style={styles.panel}>
      <ThemedText type="smallBold">Site ready for the installation?</ThemedText>
      {readiness.is_ready ? <ThemedText type="small" themeColor="textSecondary">Ready ✓</ThemedText> : null}
      {CHECKS.map(([key, label]) => (
        <ThemedView key={key} style={styles.row}>
          <Switch
            value={checks[key]} disabled={!readiness.can_check}
            onValueChange={(value) => setChecks((now) => ({ ...now, [key]: value }))}
          />
          <ThemedText type="small" style={styles.label}>{label}</ThemedText>
        </ThemedView>
      ))}
      {readiness.can_check ? (
        <>
          <TextInput
            style={[styles.input, { color: theme.text, borderColor: theme.backgroundSelected }]}
            value={checks.note} onChangeText={(note) => setChecks((now) => ({ ...now, note }))}
            placeholder="Note" placeholderTextColor={theme.textSecondary} multiline
          />
          {error ? <ThemedText type="small" themeColor="danger">{error}</ThemedText> : null}
          <TouchableOpacity onPress={save} disabled={isBusy}>
            <ThemedText type="smallBold" themeColor="primary">{isBusy ? 'Saving…' : 'Save the site checks'}</ThemedText>
          </TouchableOpacity>
        </>
      ) : null}
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  panel: { gap: Spacing.two },
  row: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two },
  label: { flex: 1 },
  input: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, minHeight: 44 },
});
