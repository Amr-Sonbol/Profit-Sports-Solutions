import DateTimePicker, { type DateTimePickerEvent } from '@react-native-community/datetimepicker';
import Ionicons from '@expo/vector-icons/Ionicons';
import { useState } from 'react';
import { ActivityIndicator, Platform, StyleSheet, TouchableOpacity } from 'react-native';

import { ApiRequestError } from '@/api/client';
import { changeTaskParts, markPartsPrepared, type PartNeededRow } from '@/api/endpoints';
import { TextInput } from '@/components/text-field';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { alert, locale, t } from '@/i18n';

/** "2026-11-20" for the server, from a picked day. */
function isoDay(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

/**
 * The team task page's Parts needed (tasks.parts_needed), as on the website:
 * each part from our stock, ordered (and when it should arrive) or arrived.
 * Its owner adds one (code, description, quantity), marks an ordered one
 * arrived, or removes one; the warehouse (or a manager) marks them prepared
 * once they're all in.
 */
export function PartsNeededPanel({ taskId, parts, prepared, canChange, canEdit = false, canPrepare, onChanged }: {
  taskId: number;
  parts: PartNeededRow[];
  prepared: boolean;
  canChange: boolean;
  /** Correcting a part: managers only. */
  canEdit?: boolean;
  canPrepare: boolean;
  onChanged: () => void;
}) {
  const theme = useTheme();
  const [adding, setAdding] = useState(false);
  // The part being corrected (a manager), or null when adding.
  const [editing, setEditing] = useState<number | null>(null);
  const [code, setCode] = useState('');
  const [description, setDescription] = useState('');
  const [quantity, setQuantity] = useState('1');
  const [source, setSource] = useState<'stock' | 'ordered'>('stock');
  const [expected, setExpected] = useState<Date | null>(null);
  const [picking, setPicking] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [isBusy, setIsBusy] = useState(false);

  const run = async (work: () => Promise<unknown>) => {
    setIsBusy(true);
    try {
      await work();
      onChanged();
    } catch (err) {
      alert('Not saved', err instanceof ApiRequestError ? err.message : 'Please try again.');
    } finally {
      setIsBusy(false);
    }
  };

  const add = async () => {
    setIsBusy(true);
    setErrors({});
    try {
      await changeTaskParts(taskId, {
        ...(editing ? { line: editing, action: 'edit' } : {}),
        product_code: code.trim(), description: description.trim(), quantity: quantity.trim() || '1', source,
        expected_on: source === 'ordered' && expected ? isoDay(expected) : '',
      });
      setEditing(null);
      setCode('');
      setDescription('');
      setQuantity('1');
      setExpected(null);
      setAdding(false);
      onChanged();
    } catch (err) {
      if (err instanceof ApiRequestError && err.status === 400 && err.body && typeof err.body === 'object') {
        setErrors(Object.fromEntries(Object.entries(err.body as Record<string, string[]>).map(([field, messages]) => [field, messages.join(' ')])));
      } else {
        alert('Not saved', 'Please try again.');
      }
    } finally {
      setIsBusy(false);
    }
  };

  const allIn = parts.length > 0 && parts.every((part) => part.state !== 'waiting');
  const inputStyle = [styles.input, { color: theme.text, borderColor: theme.backgroundSelected }];
  const chip = (value: 'stock' | 'ordered', label: string) => (
    <TouchableOpacity
      key={value} onPress={() => setSource(value)}
      style={[styles.chip, { borderColor: source === value ? theme.primary : theme.backgroundSelected },
        source === value && { backgroundColor: theme.primary }]}
    >
      <ThemedText type="small" style={source === value ? { color: theme.onPrimary } : undefined}>{label}</ThemedText>
    </TouchableOpacity>
  );

  return (
    <ThemedView style={[styles.section, { backgroundColor: theme.backgroundElement }]}>
      <ThemedText type="smallBold">Parts needed</ThemedText>
      {parts.length ? parts.map((part) => (
        <ThemedView key={part.id} style={styles.row}>
          <ThemedView style={styles.rowText}>
            <ThemedText type="small">{part.quantity} × {part.code}{part.description ? ` — ${part.description}` : ''}</ThemedText>
            <ThemedText type="small" themeColor={part.state === 'waiting' ? 'danger' : 'textSecondary'}>
              {part.state === 'stock' ? t('From our stock') : part.state === 'arrived' ? t('Arrived')
                : part.expected_on
                  ? t('Ordered — expected {date}', { date: new Date(part.expected_on).toLocaleDateString(locale(), { day: 'numeric', month: 'short' }) })
                  : t('Ordered')}
            </ThemedText>
          </ThemedView>
          {canEdit ? (
            <TouchableOpacity
              onPress={() => {
                setEditing(part.id);
                setCode(part.code);
                setDescription(part.description);
                setQuantity(String(part.quantity));
                setSource(part.state === 'stock' ? 'stock' : 'ordered');
                setExpected(part.expected_on ? new Date(part.expected_on) : null);
                setAdding(true);
              }}
              disabled={isBusy}
            >
              <ThemedText type="small" themeColor="primary">Edit</ThemedText>
            </TouchableOpacity>
          ) : null}
          {canChange && part.state === 'waiting' ? (
            <TouchableOpacity onPress={() => run(() => changeTaskParts(taskId, { line: part.id, action: 'arrived' }))} disabled={isBusy}>
              <ThemedText type="small" themeColor="primary">Arrived</ThemedText>
            </TouchableOpacity>
          ) : null}
          {canChange ? (
            <TouchableOpacity
              style={[styles.remove, { borderColor: theme.danger }]} accessibilityLabel={t('Remove {code}', { code: part.code })}
              onPress={() => run(() => changeTaskParts(taskId, { line: part.id, action: 'remove' }))} disabled={isBusy} hitSlop={8}
            >
              <Ionicons name="close" size={16} color={theme.danger} />
            </TouchableOpacity>
          ) : null}
        </ThemedView>
      )) : <ThemedText type="small" themeColor="textSecondary">None added yet.</ThemedText>}

      {parts.length ? (
        <ThemedText type="small" themeColor={prepared ? 'success' : 'textSecondary'}>
          {prepared ? 'Prepared by the warehouse.' : allIn ? 'All in — waiting for the warehouse to prepare them.' : 'Waiting for ordered parts.'}
        </ThemedText>
      ) : null}
      {canPrepare ? (
        <TouchableOpacity
          style={[styles.button, { backgroundColor: theme.primary }]} disabled={isBusy}
          onPress={() => run(() => markPartsPrepared(taskId))}
        >
          <ThemedText type="smallBold" style={{ color: theme.onPrimary }}>Mark the parts prepared</ThemedText>
        </TouchableOpacity>
      ) : null}

      {canChange && !adding ? (
        <TouchableOpacity onPress={() => setAdding(true)}>
          <ThemedText themeColor="primary">+ Add a part</ThemedText>
        </TouchableOpacity>
      ) : null}
      {canChange && adding ? (
        <ThemedView style={styles.form}>
          <TextInput style={inputStyle} value={code} onChangeText={setCode} placeholder={t('Code')} autoCapitalize="characters" autoCorrect={false} />
          {errors.product_code ? <ThemedText type="small" themeColor="danger">{errors.product_code}</ThemedText> : null}
          <TextInput style={inputStyle} value={description} onChangeText={setDescription} placeholder={t('Description (left empty: the catalogue’s)')} />
          <TextInput style={inputStyle} value={quantity} onChangeText={setQuantity} placeholder={t('Quantity')} keyboardType="number-pad" />
          {errors.quantity ? <ThemedText type="small" themeColor="danger">{errors.quantity}</ThemedText> : null}
          <ThemedView style={styles.chips}>
            {chip('stock', t('From our stock'))}
            {chip('ordered', t('Ordered'))}
          </ThemedView>
          {source === 'ordered' ? (
            <TouchableOpacity onPress={() => setPicking(true)}>
              <ThemedText type="small" themeColor="primary">
                {expected
                  ? t('Expected {date} — change', { date: expected.toLocaleDateString(locale(), { day: 'numeric', month: 'short' }) })
                  : 'Set the expected date (if known)'}
              </ThemedText>
            </TouchableOpacity>
          ) : null}
          {picking ? (
            <DateTimePicker
              value={expected ?? new Date()} mode="date" display={Platform.OS === 'ios' ? 'inline' : 'default'}
              onChange={(event: DateTimePickerEvent, date?: Date) => {
                setPicking(false);
                if (event.type !== 'dismissed' && date) setExpected(date);
              }}
            />
          ) : null}
          {isBusy ? <ActivityIndicator /> : (
            <ThemedView style={styles.chips}>
              <TouchableOpacity style={[styles.button, { backgroundColor: theme.primary }]} onPress={add} disabled={!code.trim()}>
                <ThemedText type="smallBold" style={{ color: theme.onPrimary }}>{editing ? 'Save changes' : 'Add part'}</ThemedText>
              </TouchableOpacity>
              <TouchableOpacity style={[styles.button, { borderColor: theme.backgroundSelected, borderWidth: 1 }]} onPress={() => {
                setAdding(false);
                setEditing(null);
              }}>
                <ThemedText type="small">Cancel</ThemedText>
              </TouchableOpacity>
            </ThemedView>
          )}
        </ThemedView>
      ) : null}
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  section: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.two },
  row: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two, backgroundColor: 'transparent' },
  rowText: { flex: 1, backgroundColor: 'transparent' },
  remove: { borderWidth: 1, borderRadius: Spacing.one, padding: 2 },
  form: { gap: Spacing.two, backgroundColor: 'transparent' },
  input: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, fontSize: 16 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  chip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: Spacing.three, paddingVertical: Spacing.one },
  button: { borderRadius: Spacing.two, paddingHorizontal: Spacing.three, paddingVertical: Spacing.two, alignItems: 'center' },
});
