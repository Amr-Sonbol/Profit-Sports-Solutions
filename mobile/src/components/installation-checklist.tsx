import Ionicons from '@expo/vector-icons/Ionicons';
import { Image } from 'expo-image';
import * as ImagePicker from 'expo-image-picker';
import { useState } from 'react';
import { ActivityIndicator, Alert, StyleSheet, TextInput, TouchableOpacity } from 'react-native';

import { ApiRequestError } from '@/api/client';
import { uploadLinePhoto } from '@/api/endpoints';
import { BarcodeScanner } from '@/components/barcode-scanner';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { ChecklistRow, Choice } from '@/types';

const COLORS = [['frame', 'Frame'], ['arm', 'Arm'], ['padding', 'Padding'], ['trim', 'Trim']] as const;

export function emptyChecklistRow(): ChecklistRow {
  return {
    id: null, product_code: '', status: '', serial_number: '', frame: 'Black', arm: 'Black', padding: 'Black',
    trim: 'Black', replacement: '', note: '', photo_url: '', added_on_site: true,
  };
}

/**
 * An installation's on-site checklist (the report's step 1): each machine
 * from the delivery note — what happened, its serial (typed or scanned
 * from the plate), its colours (black unless custom), what went in
 * instead, a note and a photo — and machines found on site that weren't
 * on the paperwork. Same rules as the website (tasks.forms
 * .TaskProductCheckForm); this only guides, the server decides.
 */
export function InstallationChecklist({ taskId, rows, statuses, onChange }: {
  taskId: number; rows: ChecklistRow[]; statuses: Choice[]; onChange: (rows: ChecklistRow[]) => void;
}) {
  const theme = useTheme();
  const [scanning, setScanning] = useState<number | null>(null);
  const [uploading, setUploading] = useState<number | null>(null);

  const update = (index: number, changes: Partial<ChecklistRow>) => {
    onChange(rows.map((row, i) => (i === index ? { ...row, ...changes } : row)));
  };

  const addPhoto = async (index: number) => {
    const row = rows[index];
    if (!row.id) {
      Alert.alert('Photo', 'Send the report once first; then the new machine can get its photo.');
      return;
    }
    const permission = await ImagePicker.requestCameraPermissionsAsync();
    if (!permission.granted) {
      Alert.alert('Permission required', 'Camera access is needed.');
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
      Alert.alert('Photo not saved', err instanceof ApiRequestError ? err.message : 'Please try again.');
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
        const needsSerial = row.status === 'done' && !row.serial_number.trim();
        const needsReason = (row.status === 'damaged' || row.status === 'wrong_item') && !row.note.trim() && !row.photo_url;
        return (
          <ThemedView
            key={row.id ?? `new-${index}`}
            style={[styles.card, { borderColor: !row.status || needsSerial || needsReason ? theme.primary : theme.backgroundSelected }]}
          >
            {row.id && !row.added_on_site ? (
              <ThemedText type="smallBold">{row.product_code}</ThemedText>
            ) : (
              <>
                <ThemedText type="smallBold">{row.added_on_site && row.id ? 'Added on site' : 'Machine not on the delivery note'}</ThemedText>
                <TextInput
                  style={inputStyle} value={row.product_code} onChangeText={(value) => update(index, { product_code: value })}
                  placeholder="Product code / model" placeholderTextColor={theme.textSecondary} autoCapitalize="characters"
                />
              </>
            )}

            <ThemedView style={styles.chips}>
              {statuses.map((choice) => {
                const on = row.status === choice.value;
                return (
                  <TouchableOpacity
                    key={choice.value}
                    style={[styles.chip, { borderColor: on ? theme.primary : theme.backgroundSelected },
                      on && { backgroundColor: theme.primary }]}
                    onPress={() => update(index, { status: choice.value })}
                  >
                    <ThemedText type="small" style={on ? { color: theme.onPrimary } : undefined}>{choice.label}</ThemedText>
                  </TouchableOpacity>
                );
              })}
            </ThemedView>

            <ThemedView style={styles.serialRow}>
              <TextInput
                style={[inputStyle, styles.flex, needsSerial && { borderColor: theme.danger }]}
                value={row.serial_number} onChangeText={(value) => update(index, { serial_number: value })}
                placeholder="Serial number (from the plate)" placeholderTextColor={theme.textSecondary}
                autoCapitalize="characters" autoCorrect={false}
              />
              <TouchableOpacity style={[styles.iconButton, { borderColor: theme.primary }]} onPress={() => setScanning(index)}>
                <Ionicons name="barcode-outline" size={22} color={theme.primary} />
              </TouchableOpacity>
            </ThemedView>
            {needsSerial ? (
              <ThemedText type="small" themeColor="danger">An installed machine needs its serial number — type or scan it.</ThemedText>
            ) : null}

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

            <TextInput
              style={inputStyle} value={row.replacement} onChangeText={(value) => update(index, { replacement: value })}
              placeholder="Installed instead (if a different item went in)" placeholderTextColor={theme.textSecondary}
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
        <ThemedText themeColor="primary">+ A machine not on the delivery note</ThemedText>
      </TouchableOpacity>

      <BarcodeScanner
        visible={scanning !== null}
        onClose={() => setScanning(null)}
        onScanned={(text) => {
          if (scanning !== null) {
            update(scanning, { serial_number: text });
          }
          setScanning(null);
        }}
      />
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  list: { gap: Spacing.two, backgroundColor: 'transparent' },
  card: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.two },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.one, backgroundColor: 'transparent' },
  chip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: Spacing.two, paddingVertical: Spacing.one },
  input: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, fontSize: 15 },
  note: { minHeight: 50, textAlignVertical: 'top' },
  flex: { flex: 1 },
  serialRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two, backgroundColor: 'transparent' },
  iconButton: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two },
  colors: { flexDirection: 'row', gap: Spacing.one, backgroundColor: 'transparent' },
  color: { flex: 1, gap: 2, backgroundColor: 'transparent' },
  photo: { width: 56, height: 56, borderRadius: Spacing.one },
  photoButton: { borderWidth: 1, borderRadius: Spacing.two, paddingHorizontal: Spacing.three, paddingVertical: Spacing.two },
  addButton: { borderWidth: 1, borderStyle: 'dashed', borderRadius: Spacing.two, padding: Spacing.three, alignItems: 'center' },
});
