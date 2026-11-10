import { Image } from 'expo-image';
import { StyleSheet } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { useTheme } from '@/hooks/use-theme';

/** "Tarek Mansour" -> "TM", for someone with no photo yet. */
export function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((word) => word[0]?.toUpperCase()).join('');
}

/** A person's photo, round — or their initials until they add one. */
export function Avatar({ name, url, size = 40 }: { name: string; url?: string; size?: number }) {
  const theme = useTheme();
  const round = { width: size, height: size, borderRadius: size / 2 };
  return url ? (
    <Image source={{ uri: url }} style={round} contentFit="cover" />
  ) : (
    <ThemedView style={[round, styles.initials, { backgroundColor: theme.backgroundSelected }]}>
      <ThemedText type="smallBold">{initials(name)}</ThemedText>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  initials: { alignItems: 'center', justifyContent: 'center' },
});
