import { CameraView, useCameraPermissions } from 'expo-camera';
import { useRef } from 'react';
import { Modal, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

/**
 * Reads a serial number off a machine's plate with the camera — the
 * plate's barcode (most use Code 128 / Code 39) or a QR code — and hands
 * the text back, so it isn't typed (and mistyped) by hand. The first read
 * wins; the scanner closes itself.
 */
export function BarcodeScanner({ visible, onScanned, onClose }: {
  visible: boolean; onScanned: (text: string) => void; onClose: () => void;
}) {
  const theme = useTheme();
  const [permission, requestPermission] = useCameraPermissions();
  // A plate stays in view for a moment: only its first read counts.
  const done = useRef(false);

  return (
    <Modal
      visible={visible} animationType="slide"
      onShow={() => {
        done.current = false;
      }}
      onRequestClose={onClose}
    >
      <ThemedView style={styles.container}>
        <SafeAreaView style={styles.container}>
          <ThemedView style={styles.bar}>
            <ThemedText type="subtitle">Scan the serial plate</ThemedText>
            <TouchableOpacity onPress={onClose}>
              <ThemedText themeColor="primary">Close</ThemedText>
            </TouchableOpacity>
          </ThemedView>
          {!permission ? null : !permission.granted ? (
            <ThemedView style={styles.message}>
              <ThemedText>The camera is needed to scan the plate.</ThemedText>
              <TouchableOpacity style={[styles.button, { backgroundColor: theme.primary }]} onPress={requestPermission}>
                <ThemedText style={{ color: theme.onPrimary }}>Allow the camera</ThemedText>
              </TouchableOpacity>
            </ThemedView>
          ) : (
            <CameraView
              style={styles.camera}
              barcodeScannerSettings={{ barcodeTypes: ['code128', 'code39', 'code93', 'ean13', 'ean8', 'upc_a', 'datamatrix', 'qr'] }}
              onBarcodeScanned={({ data }) => {
                if (!done.current && data) {
                  done.current = true;
                  onScanned(data.trim());
                }
              }}
            />
          )}
          <ThemedText type="small" themeColor="textSecondary" style={styles.hint}>
            Hold the plate&apos;s barcode inside the frame. No barcode? Close this and type the number.
          </ThemedText>
        </SafeAreaView>
      </ThemedView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  bar: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: Spacing.three, paddingVertical: Spacing.three,
  },
  camera: { flex: 1 },
  message: { padding: Spacing.four, gap: Spacing.three, alignItems: 'center' },
  button: { borderRadius: Spacing.two, paddingHorizontal: Spacing.four, paddingVertical: Spacing.two },
  hint: { padding: Spacing.three, textAlign: 'center' },
});
