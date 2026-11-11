import Ionicons from '@expo/vector-icons/Ionicons';
import * as Notifications from 'expo-notifications';
import { useCallback, useState } from 'react';
import { useFocusEffect } from 'expo-router';
import { Linking, StyleSheet, TouchableOpacity } from 'react-native';

import { ApiRequestError, apiRequest } from '@/api/client';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { registerForPush } from '@/push';

/**
 * Profile → Notifications: whether this phone may show them (with Settings
 * one tap away when not), and "Send me a test notification" — registering
 * the phone again first, then a real one from the server, so a person can
 * see for themselves that notifications reach them.
 */
export function NotificationCheck() {
  const theme = useTheme();
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useFocusEffect(useCallback(() => {
    Notifications.getPermissionsAsync().then(({ status }) => setAllowed(status === 'granted')).catch(() => setAllowed(null));
  }, []));

  const test = async () => {
    setBusy(true);
    setResult(null);
    try {
      await registerForPush();  // asks for permission if it never was, and registers this phone again
      const { status } = await Notifications.getPermissionsAsync();
      setAllowed(status === 'granted');
      if (status !== 'granted') {
        setResult({ ok: false, text: 'Notifications are off for this app. Turn them on in Settings, then try again.' });
        return;
      }
      await apiRequest<{ phones: number }>('/api/push-device/test/', { method: 'POST', body: {} });
      setResult({ ok: true, text: 'Sent. It should arrive within a few seconds — if it doesn’t, tell your manager.' });
    } catch (err) {
      const detail = err instanceof ApiRequestError && err.body && typeof err.body === 'object' && 'detail' in err.body
        ? String((err.body as { detail: unknown }).detail) : 'Could not send it. Check your connection and try again.';
      setResult({ ok: false, text: detail });
    } finally {
      setBusy(false);
    }
  };

  return (
    <ThemedView style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
      <ThemedView style={styles.row}>
        <Ionicons name="notifications-outline" size={20} color={theme.textSecondary} />
        <ThemedText style={styles.grow}>Notifications</ThemedText>
        <ThemedText type="smallBold" themeColor={allowed === false ? 'danger' : allowed ? 'primary' : 'textSecondary'}>
          {allowed === null ? '…' : allowed ? 'On' : 'Off'}
        </ThemedText>
      </ThemedView>
      {allowed === false ? (
        <TouchableOpacity onPress={() => Linking.openSettings()}>
          <ThemedText type="small" themeColor="primary">Turn them on in Settings ›</ThemedText>
        </TouchableOpacity>
      ) : null}
      <TouchableOpacity
        style={[styles.button, { borderColor: theme.primary }, busy && styles.dim]} onPress={test} disabled={busy}
      >
        <ThemedText type="small" themeColor="primary">{busy ? 'Sending…' : 'Send me a test notification'}</ThemedText>
      </TouchableOpacity>
      {result ? <ThemedText type="small" themeColor={result.ok ? 'primary' : 'danger'}>{result.text}</ThemedText> : null}
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.two },
  row: { flexDirection: 'row', alignItems: 'center', gap: Spacing.three, backgroundColor: 'transparent' },
  grow: { flex: 1 },
  button: { borderWidth: 1, borderRadius: Spacing.two, paddingVertical: Spacing.two, alignItems: 'center' },
  dim: { opacity: 0.5 },
});
