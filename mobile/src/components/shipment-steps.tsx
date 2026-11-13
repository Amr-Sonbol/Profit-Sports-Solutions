import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Linking, StyleSheet, TouchableOpacity } from 'react-native';
import { TextInput } from '@/components/text-field';

import { ApiRequestError } from '@/api/client';
import { taskAction } from '@/api/endpoints';
import { confirmAction } from '@/confirm';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { TeamTaskDetail } from '@/types';
import { alert } from '@/i18n';

/**
 * A part shipment on the team view, as on the office's task page: where it
 * goes, its parts, and the steps this person can take — the warehouse
 * manager's "Ready to ship" (parts, PAK number, who ships it), "Shipped"
 * (courier and tracking; the customer is told), and the fitting visit.
 */
export function ShipmentSteps({ task, onChanged }: { task: TeamTaskDetail; onChanged: (task: TeamTaskDetail) => void }) {
  const router = useRouter();
  const theme = useTheme();
  const shipment = task.shipment;
  const [rows, setRows] = useState<{ code: string; quantity: string }[]>(
    shipment?.parts.length ? shipment.parts.map((part) => ({ code: part.code, quantity: String(part.quantity) })) : [{ code: '', quantity: '1' }],
  );
  const [pak, setPak] = useState(shipment?.pak_reference_number ?? '');
  const [shipperId, setShipperId] = useState<number | null>(null);
  const [company, setCompany] = useState('');
  const [tracking, setTracking] = useState('');
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState('');

  if (!shipment) {
    return null;
  }
  const inputStyle = [styles.input, { color: theme.text, borderColor: theme.backgroundSelected }];

  const act = async (body: Record<string, unknown>, question: [string, string], done: string) => {
    if (!(await confirmAction(question[0], question[1]))) {
      return null;
    }
    setIsBusy(true);
    setError('');
    try {
      const result = await taskAction(task.id, body as Record<string, string | number>);
      alert('Done', done);
      return result;
    } catch (err) {
      const errBody = err instanceof ApiRequestError ? (err.body as Record<string, unknown> | null) : null;
      const first = errBody && typeof errBody === 'object' ? Object.values(errBody).flat()[0] : null;
      setError(first ? String(first) : 'The request could not be completed. Please try again.');
      return null;
    } finally {
      setIsBusy(false);
    }
  };

  const ready = async () => {
    const result = await act(
      { action: 'shipment_ready', parts: rows, pak_reference_number: pak, technician: shipperId ?? '' },
      ['Ready to ship?', 'The technician who ships it is told.'], 'Ready to ship. The technician has been told.',
    );
    if (result) {
      onChanged(result);
    }
  };

  // From the factory straight to the customer: the PAK number, courier and tracking — shipped, nothing from our stock.
  const shippedDirect = async () => {
    const result = await act(
      { action: 'shipment_direct', pak_reference_number: pak, shipping_company: company, shipping_tracking_number: tracking },
      ['Shipped direct from the factory?', 'The customer is told, with the tracking number.'],
      'Shipped. The customer has been told.',
    );
    if (result) {
      onChanged(result);
    }
  };

  const shipped = async () => {
    const result = await act(
      { action: 'shipment_shipped', shipping_company: company, shipping_tracking_number: tracking },
      ['Mark as shipped?', 'The customer is told, with the tracking number.'], 'Shipped. The customer has been told.',
    );
    if (result) {
      onChanged(result);
    }
  };

  const bookFitting = async () => {
    const result = (await act(
      { action: 'book_fitting' }, ['Book the fitting visit?', 'A visit task is created to fit the part.'],
      'Fitting visit created: choose its technician and day.',
    )) as unknown as { fitting_task_id?: number } | null;
    if (result?.fitting_task_id) {
      router.push(`/team-task/${result.fitting_task_id}`);
    }
  };

  return (
    <ThemedView style={styles.section}>
      <ThemedText type="smallBold">Part shipment · {shipment.label}</ThemedText>
      <ThemedText>{shipment.send_to.customer} — {shipment.send_to.branch}</ThemedText>
      {shipment.send_to.address ? (
        <TouchableOpacity onPress={() => Linking.openURL(shipment.send_to.directions_url)}>
          <ThemedText themeColor="primary">{shipment.send_to.address}</ThemedText>
        </TouchableOpacity>
      ) : null}
      {shipment.send_to.contact_name ? <ThemedText>{shipment.send_to.contact_name}</ThemedText> : null}
      {shipment.send_to.contact_phone ? (
        <TouchableOpacity onPress={() => Linking.openURL(`tel:${shipment.send_to.contact_phone}`)}>
          <ThemedText themeColor="primary">{shipment.send_to.contact_phone}</ThemedText>
        </TouchableOpacity>
      ) : null}
      {shipment.parts.map((part) => (
        <ThemedText key={part.code} type="small">{part.code} × {part.quantity}</ThemedText>
      ))}
      {shipment.pak_reference_number ? <ThemedText type="small">PAK: {shipment.pak_reference_number}</ThemedText> : null}
      {shipment.shipper ? <ThemedText type="small">Ships it: {shipment.shipper}</ThemedText> : null}
      {shipment.shipping_tracking_number ? (
        <ThemedText type="small">{shipment.shipping_company} · {shipment.shipping_tracking_number}</ThemedText>
      ) : null}

      {shipment.can_prepare && !shipment.direct_only ? (
        <ThemedView style={[styles.box, { borderColor: theme.primary }]}>
          <ThemedText type="smallBold">Ready to ship</ThemedText>
          {rows.map((row, index) => (
            <ThemedView key={index} style={styles.row}>
              <TextInput
                style={[inputStyle, styles.code]} value={row.code} placeholder="Part code" autoCapitalize="characters"
                placeholderTextColor={theme.textSecondary}
                onChangeText={(value) => setRows((all) => all.map((r, i) => (i === index ? { ...r, code: value } : r)))}
              />
              <TextInput
                style={[inputStyle, styles.quantity]} value={row.quantity} keyboardType="number-pad"
                onChangeText={(value) => setRows((all) => all.map((r, i) => (i === index ? { ...r, quantity: value } : r)))}
              />
            </ThemedView>
          ))}
          <TouchableOpacity onPress={() => setRows((all) => [...all, { code: '', quantity: '1' }])}>
            <ThemedText type="small" themeColor="primary">+ Add a part</ThemedText>
          </TouchableOpacity>
          <TextInput
            style={inputStyle} value={pak} onChangeText={setPak} placeholder="Panatta PAK (optional)"
            placeholderTextColor={theme.textSecondary}
          />
          <ThemedText type="small">Who ships it</ThemedText>
          <ThemedView style={styles.row}>
            {(shipment.shippers ?? []).map((person) => (
              <TouchableOpacity
                key={person.id}
                style={[styles.chip, { borderColor: theme.primary }, shipperId === person.id && { backgroundColor: theme.primary }]}
                onPress={() => setShipperId(person.id)}
              >
                <ThemedText type="small" style={shipperId === person.id ? { color: theme.onPrimary } : undefined}>{person.name}</ThemedText>
              </TouchableOpacity>
            ))}
          </ThemedView>
          <Button label="Ready to ship" onPress={ready} disabled={isBusy} />
        </ThemedView>
      ) : null}

      {shipment.can_ship_direct ? (
        <ThemedView style={[styles.box, { borderColor: theme.primary }]}>
          <ThemedText type="smallBold">Sent direct from the factory</ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            The factory ships it straight to the customer: nothing leaves our stock.
          </ThemedText>
          <TextInput
            style={inputStyle} value={pak} onChangeText={setPak} placeholder="Panatta PAK"
            placeholderTextColor={theme.textSecondary} autoCapitalize="characters"
          />
          <ThemedView style={styles.row}>
            {shipment.companies.map((option) => (
              <TouchableOpacity
                key={option.value}
                style={[styles.chip, { borderColor: theme.primary }, company === option.value && { backgroundColor: theme.primary }]}
                onPress={() => setCompany(option.value)}
              >
                <ThemedText type="small" style={company === option.value ? { color: theme.onPrimary } : undefined}>{option.label}</ThemedText>
              </TouchableOpacity>
            ))}
          </ThemedView>
          <TextInput
            style={inputStyle} value={tracking} onChangeText={setTracking} placeholder="Tracking number (optional)"
            placeholderTextColor={theme.textSecondary}
          />
          <Button label="Shipped direct" onPress={shippedDirect} disabled={isBusy || !company} />
        </ThemedView>
      ) : null}
      {shipment.ships_direct ? <ThemedText type="small" themeColor="textSecondary">Sent direct from the factory.</ThemedText> : null}

      {shipment.can_ship && !shipment.direct_only ? (
        <ThemedView style={[styles.box, { borderColor: theme.primary }]}>
          <ThemedText type="smallBold">Shipped</ThemedText>
          <ThemedView style={styles.row}>
            {shipment.companies.map((option) => (
              <TouchableOpacity
                key={option.value}
                style={[styles.chip, { borderColor: theme.primary }, company === option.value && { backgroundColor: theme.primary }]}
                onPress={() => setCompany(option.value)}
              >
                <ThemedText type="small" style={company === option.value ? { color: theme.onPrimary } : undefined}>{option.label}</ThemedText>
              </TouchableOpacity>
            ))}
          </ThemedView>
          <TextInput
            style={inputStyle} value={tracking} onChangeText={setTracking} placeholder="Tracking number (optional)"
            placeholderTextColor={theme.textSecondary}
          />
          <Button label="Mark as shipped" onPress={shipped} disabled={isBusy || !company} />
        </ThemedView>
      ) : null}

      {shipment.can_book_fitting ? <Button label="Book the fitting visit" onPress={bookFitting} disabled={isBusy} /> : null}
      {error ? <ThemedText themeColor="danger" type="small">{error}</ThemedText> : null}
    </ThemedView>
  );
}

function Button({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) {
  const theme = useTheme();
  return (
    <TouchableOpacity style={[styles.button, { borderColor: theme.primary }, disabled && styles.disabled]} onPress={onPress} disabled={disabled}>
      <ThemedText type="small" themeColor="primary">{label}</ThemedText>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  section: { gap: Spacing.two, backgroundColor: 'transparent' },
  box: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.two },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  input: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two },
  code: { flex: 1, minWidth: 140 },
  quantity: { width: 70 },
  chip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: Spacing.three, paddingVertical: Spacing.one },
  button: { borderWidth: 1, borderRadius: Spacing.two, paddingVertical: Spacing.two, paddingHorizontal: Spacing.three, alignItems: 'center' },
  disabled: { opacity: 0.5 },
});
