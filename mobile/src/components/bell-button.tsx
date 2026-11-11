import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { StyleSheet, TouchableOpacity, View } from 'react-native';

import { fetchBell } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { t } from '@/i18n';

/** The bell at the top of a tab: how many new items, a tap opens them (the website bell's). */
export function BellButton() {
  const router = useRouter();
  const theme = useTheme();
  const [count, setCount] = useState(0);

  useFocusEffect(useCallback(() => {
    let active = true;
    const load = () => fetchBell().then((bell) => active && setCount(bell.count)).catch(() => undefined);
    load();
    // While the tab is open, new items show without leaving it.
    const timer = setInterval(load, 30000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []));

  return (
    <TouchableOpacity
      onPress={() => router.push('/bell')} style={styles.button} hitSlop={8}
      accessibilityRole="button" accessibilityLabel={count ? t('Notifications, {count} new', { count }) : 'Notifications'}
    >
      <Ionicons name={count ? 'notifications' : 'notifications-outline'} size={24} color={theme.text} />
      {count ? (
        <View style={[styles.badge, { backgroundColor: theme.danger }]}>
          <ThemedText style={styles.badgeText}>{count > 99 ? '99+' : count}</ThemedText>
        </View>
      ) : null}
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  button: { padding: Spacing.one },
  badge: {
    position: 'absolute', top: -2, end: -4, minWidth: 18, height: 18, borderRadius: 9,
    alignItems: 'center', justifyContent: 'center', paddingHorizontal: 4,
  },
  badgeText: { color: '#fff', fontSize: 11, lineHeight: 14, fontWeight: '700' },
});
