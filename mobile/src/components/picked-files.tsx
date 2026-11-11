import Ionicons from '@expo/vector-icons/Ionicons';
import { StyleSheet, TouchableOpacity } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { t } from '@/i18n';
import { MAX_FILES, type PickedFile, pickDocuments, pickPhotos } from '@/pick-files';

/**
 * Files to send with a message: "Photos" and "Files" add them (up to 10),
 * each picked one shows its name with ✕ to take it back.
 */
export function PickedFiles({ files, onChange, disabled }: {
  files: PickedFile[]; onChange: (files: PickedFile[]) => void; disabled?: boolean;
}) {
  const theme = useTheme();
  const room = MAX_FILES - files.length;
  const add = async (pick: (limit: number) => Promise<PickedFile[]>) => {
    if (room > 0) {
      onChange([...files, ...(await pick(room))]);
    }
  };
  return (
    <ThemedView style={styles.box}>
      {files.map((file, index) => (
        <ThemedView key={`${file.uri}-${index}`} style={[styles.file, { borderColor: theme.backgroundSelected }]}>
          <Ionicons name={file.mimeType.startsWith('image/') ? 'image-outline' : 'document-outline'} size={18} color={theme.textSecondary} />
          <ThemedText type="small" numberOfLines={1} style={styles.flex}>{file.name}</ThemedText>
          <TouchableOpacity
            accessibilityLabel={t('Remove')} disabled={disabled}
            onPress={() => onChange(files.filter((_, other) => other !== index))}
          >
            <Ionicons name="close" size={20} color={theme.danger} />
          </TouchableOpacity>
        </ThemedView>
      ))}
      {room > 0 ? (
        <ThemedView style={styles.row}>
          <TouchableOpacity style={styles.add} onPress={() => add(pickPhotos)} disabled={disabled}>
            <Ionicons name="images-outline" size={20} color={theme.primary} />
            <ThemedText type="small" themeColor="primary">Photos</ThemedText>
          </TouchableOpacity>
          <TouchableOpacity style={styles.add} onPress={() => add(pickDocuments)} disabled={disabled}>
            <Ionicons name="attach" size={20} color={theme.primary} />
            <ThemedText type="small" themeColor="primary">Files</ThemedText>
          </TouchableOpacity>
        </ThemedView>
      ) : null}
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  box: { gap: Spacing.one, backgroundColor: 'transparent' },
  row: { flexDirection: 'row', gap: Spacing.three, backgroundColor: 'transparent' },
  add: { flexDirection: 'row', alignItems: 'center', gap: Spacing.one, paddingVertical: Spacing.one },
  file: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.two, borderWidth: 1, borderRadius: Spacing.two,
    paddingHorizontal: Spacing.two, paddingVertical: Spacing.one, backgroundColor: 'transparent',
  },
});
