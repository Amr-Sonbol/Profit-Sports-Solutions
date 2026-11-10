import { StyleSheet } from 'react-native';

import { ThemedText } from '@/components/themed-text';

/** Shown when a screen is the last copy kept on the phone (offline.withOfflineCopy). */
export function OfflineNotice({ offline }: { offline: boolean }) {
  if (!offline) {
    return null;
  }
  return (
    <ThemedText type="small" themeColor="danger" style={styles.notice}>
      No connection — this is how it was when last loaded. Changes need a connection.
    </ThemedText>
  );
}

const styles = StyleSheet.create({
  notice: { paddingVertical: 4 },
});
