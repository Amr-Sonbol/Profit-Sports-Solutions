import { Image } from 'expo-image';
import * as ImagePicker from 'expo-image-picker';
import { useState } from 'react';
import { ActivityIndicator, StyleSheet, TouchableOpacity } from 'react-native';
import { TextInput } from '@/components/text-field';

import { ApiRequestError } from '@/api/client';
import { uploadLinePhoto } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { ChecklistRow, Choice } from '@/types';
import { alert } from '@/i18n';

const COLORS = [['frame', 'Frame'], ['arm', 'Arm'], ['padding', 'Padding'], ['trim', 'Trim']] as const;

const KINDS = [['machine', 'Machine'], ['part', 'Spare part']] as const;
// Receiving, when fewer arrived than listed: what happened to the rest.
const REST_STATUSES = [['missing', 'Missing'], ['damaged', 'Damaged']] as const;

export function emptyChecklistRow(): ChecklistRow {
  return {
    id: null, product_code: '', kind: 'machine', quantity: '1', status: '', serial_number: '', frame: 'Black',
    arm: 'Black', padding: 'Black', trim: 'Black', replacement: '', note: '', photo_url: '', added_on_site: true,
  };
}

/**
 * An equipment step's checklist (the report's step 1): each line from the
 * delivery note — what happened, its serial (typed from the sticker), its
 * colours (black unless custom), what went in instead, a note and a
 * photo — and anything found that wasn't on the paperwork. Receiving
 * counts by code instead: no serial, how many arrived, and whether the
 * rest is missing or damaged; a spare part has no colours. Only an
 * installation needs every serial. Same rules as the website
 * (tasks.forms.TaskProductCheckForm); this only guides, the server decides.
 */
export function InstallationChecklist({ taskId, step, rows, statuses, onChange }: {
  taskId: number; step: string; rows: ChecklistRow[]; statuses: Choice[]; onChange: (rows: ChecklistRow[]) => void;
}) {
  const receiving = step === 'receiving';
  const theme = useTheme();
  const [uploading, setUploading] = useState<number | null>(null);

  const update = (index: number, changes: Partial<ChecklistRow>) => {
    onChange(rows.map((row, i) => (i === index ? { ...row, ...changes } : row)));
  };

  const addPhoto = async (index: number) => {
    const row = rows[index];
    if (!row.id) {
      alert('Photo', 'Send the report once first; then the new machine can get its photo.');
      return;
    }
    const permission = await ImagePicker.requestCameraPermissionsAsync();
    if (!permission.granted) {
      alert('Permission required', 'Camera access is needed.');
      return;
    }
    const result = await ImagePicker.launchCameraAsync({ mediaTypes: ['images'], quality: 0.7 });
    const asset = result.canceled ? null : result.assets[0];
    if (!asset) {
      return;
    }
    setUploading(index);
    try {
      const { photo_url } = await uploadLinePhoto(taskId, row.id, {
        uri: asset.uri, name: asset.fileName ?? 'machine.jpg', type: asset.mimeType ?? 'image/jpeg',
      });
      update(index, { photo_url });
    } catch (err) {
      alert('Photo not saved', err instanceof ApiRequestError ? err.message : 'Please try again.');
    } finally {
      setUploading(null);
    }
  };

  const inputStyle = [styles.input, { color: theme.text, borderColor: theme.backgroundSelected }];
  const checked = rows.filter((row) => row.status).length;

  return (
    <ThemedView style={styles.list}>
      <ThemedText type="small" themeColor={checked === rows.length ? 'textSecondary' : 'primary'}>
        {checked} of {rows.length} checked
      </ThemedText>
      {rows.map((row, index) => {
        const needsTest = step === 'installation' && row.status === 'done' && row.kind !== 'part' && !row.tested_working;
        const needsSerial = row.status === 'done' && !row.serial_number.trim() && (
          step === 'installation' || (step === 'loading' && row.kind !== 'part' && !row.serial_unreachable)
        );
        const listed = Number(row.quantity) || 1;
        const short = receiving && row.id !== null && row.status === 'done' && (row.received ?? '') !== ''
          && Number(row.received) < listed;
        const needsReason = (
          row.status === 'damaged' || row.status === 'wrong_item' || (short && row.rest_status === 'damaged')
        ) && !row.note.trim() && !row.photo_url;
        return (
          <ThemedView
            key={row.id ?? `new-${index}`}
            style={[styles.card, { borderColor: !row.status || needsSerial || needsReason || needsTest ? theme.primary : theme.backgroundSelected }]}
          >
            {row.id && !row.added_on_site ? (
              <ThemedText type="smallBold">
                {row.product_code}{listed > 1 ? ` × ${listed}` : ''}{row.kind === 'part' ? ' · Spare part' : ''}
              </ThemedText>
            ) : (
              <>
                <ThemedText type="smallBold">
                  {row.added_on_site && row.id ? 'Added on site' : receiving ? 'Not on the list' : 'Machine not on the delivery note'}
                </ThemedText>
                {receiving && !row.id ? (
                  <ThemedView style={styles.chips}>
                    {KINDS.map(([value, label]) => (
                      <Chip key={value} label={label} on={row.kind === value} onPress={() => update(index, { kind: value })} />
                    ))}
                  </ThemedView>
                ) : null}
                <ThemedView style={styles.serialRow}>
                  <TextInput
                    style={[inputStyle, styles.flex]} value={row.product_code}
                    onChangeText={(value) => update(index, { product_code: value })}
                    placeholder="Product code / model" placeholderTextColor={theme.textSecondary} autoCapitalize="characters"
                  />
                  {receiving && !row.id ? (
                    <TextInput
                      style={[inputStyle, styles.quantity]} value={String(row.quantity)} keyboardType="number-pad"
                      onChangeText={(value) => update(index, { quantity: value })}
                      placeholder="Qty" placeholderTextColor={theme.textSecondary}
                    />
                  ) : null}
                </ThemedView>
              </>
            )}

            <ThemedView style={styles.chips}>
              {statuses.map((choice) => (
                <Chip
                  key={choice.value} label={choice.label} on={row.status === choice.value}
                  onPress={() => update(index, { status: choice.value })}
                />
              ))}
            </ThemedView>

            {receiving && row.id && row.status === 'done' && listed > 1 ? (
              <>
                <ThemedView style={styles.serialRow}>
                  <ThemedText type="small" style={styles.flex}>How many arrived (if not all {listed})</ThemedText>
                  <TextInput
                    style={[inputStyle, styles.quantity]} value={row.received ?? ''} keyboardType="number-pad"
                    onChangeText={(value) => update(index, { received: value })}
                    placeholder={String(listed)} placeholderTextColor={theme.textSecondary}
                  />
                </ThemedView>
                {short ? (
                  <ThemedView style={styles.chips}>
                    <ThemedText type="small">The other {listed - Number(row.received)}:</ThemedText>
                    {REST_STATUSES.map(([value, label]) => (
                      <Chip
                        key={value} label={label} on={(row.rest_status || 'missing') === value}
                        onPress={() => update(index, { rest_status: value })}
                      />
                    ))}
                  </ThemedView>
                ) : null}
              </>
            ) : null}

            {!receiving ? (
              <TextInput
                style={[inputStyle, needsSerial && { borderColor: theme.danger }]}
                value={row.serial_number} onChangeText={(value) => update(index, { serial_number: value })}
                placeholder="Serial number from the sticker"
                placeholderTextColor={theme.textSecondary} autoCapitalize="characters" autoCorrect={false}
              />
            ) : null}
            {step === 'loading' && row.kind !== 'part' && !row.serial_number.trim() ? (
              <Chip
                label="Serial not reachable (packed)" on={!!row.serial_unreachable}
                onPress={() => update(index, { serial_unreachable: !row.serial_unreachable })}
              />
            ) : null}
            {step === 'installation' && row.status === 'done' && row.kind !== 'part' ? (
              <Chip
                label="Assembled, levelled and tested — working" on={!!row.tested_working}
                onPress={() => update(index, { tested_working: !row.tested_working })}
              />
            ) : null}
            {row.serial_not_taken_at_loading ? (
              <ThemedText type="small" themeColor="primary">Serial not taken at loading — read it from the sticker.</ThemedText>
            ) : null}
            {needsSerial ? (
              <ThemedText type="small" themeColor="danger">
                {step === 'loading'
                  ? 'Type the serial from the sticker, or tick “Serial not reachable (packed)”.'
                  : 'This machine needs its serial number — type it from the sticker.'}
              </ThemedText>
            ) : null}

            {row.kind !== 'part' ? (
              <ThemedView style={styles.colors}>
                {COLORS.map(([field, label]) => (
                  <ThemedView key={field} style={styles.color}>
                    <ThemedText type="small" themeColor="textSecondary">{label}</ThemedText>
                    <TextInput
                      style={inputStyle} value={row[field]} onChangeText={(value) => update(index, { [field]: value })}
                      placeholder="Black" placeholderTextColor={theme.textSecondary}
                    />
                  </ThemedView>
                ))}
              </ThemedView>
            ) : null}

            <TextInput
              style={inputStyle} value={row.replacement} onChangeText={(value) => update(index, { replacement: value })}
              placeholder="Instead (if a different item came or went in)" placeholderTextColor={theme.textSecondary}
            />
            <TextInput
              style={[inputStyle, styles.note, needsReason && { borderColor: theme.danger }]} value={row.note} multiline
              onChangeText={(value) => update(index, { note: value })}
              placeholder={needsReason ? 'Say what is wrong, or add a photo' : 'Note (optional)'}
              placeholderTextColor={needsReason ? theme.danger : theme.textSecondary}
            />

            <ThemedView style={styles.serialRow}>
              {row.photo_url ? <Image source={{ uri: row.photo_url }} style={styles.photo} contentFit="cover" /> : null}
              <TouchableOpacity style={[styles.photoButton, { borderColor: theme.primary }]} onPress={() => addPhoto(index)}>
                {uploading === index ? <ActivityIndicator /> : (
                  <ThemedText type="small" themeColor="primary">{row.photo_url ? 'Retake photo' : 'Take a photo'}</ThemedText>
                )}
              </TouchableOpacity>
              {!row.id || row.added_on_site ? (
                <TouchableOpacity onPress={() => onChange(rows.filter((_, i) => i !== index))}>
                  <ThemedText type="small" themeColor="danger">Remove</ThemedText>
                </TouchableOpacity>
              ) : null}
            </ThemedView>
          </ThemedView>
        );
      })}
      <TouchableOpacity
        style={[styles.addButton, { borderColor: theme.primary }]} onPress={() => onChange([...rows, emptyChecklistRow()])}
      >
        <ThemedText themeColor="primary">{receiving ? '+ Something not on the list' : '+ A machine not on the delivery note'}</ThemedText>
      </TouchableOpacity>
    </ThemedView>
  );
}

function Chip({ label, on, onPress }: { label: string; on: boolean; onPress: () => void }) {
  const theme = useTheme();
  return (
    <TouchableOpacity
      style={[styles.chip, { borderColor: on ? theme.primary : theme.backgroundSelected }, on && { backgroundColor: theme.primary }]}
      onPress={onPress}
    >
      <ThemedText type="small" style={on ? { color: theme.onPrimary } : undefined}>{label}</ThemedText>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  quantity: { width: 64 },
  list:{ gap: Spacing.two, backgroundColor: 'transparent' },
  card: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.two },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.one, backgroundColor: 'transparent' },
  chip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: Spacing.two, paddingVertical: Spacing.one },
  input: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, fontSize: 15 },
  note: { minHeight: 50, textAlignVertical: 'top' },
  flex: { flex: 1 },
  serialRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two, backgroundColor: 'transparent' },
  colors: { flexDirection: 'row', gap: Spacing.one, backgroundColor: 'transparent' },
  color: { flex: 1, gap: 2, backgroundColor: 'transparent' },
  photo: { width: 56, height: 56, borderRadius: Spacing.one },
  photoButton: { borderWidth: 1, borderRadius: Spacing.two, paddingHorizontal: Spacing.three, paddingVertical: Spacing.two },
  addButton: { borderWidth: 1, borderStyle: 'dashed', borderRadius: Spacing.two, padding: Spacing.three, alignItems: 'center' },
});
