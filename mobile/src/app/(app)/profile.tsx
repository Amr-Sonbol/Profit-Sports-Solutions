import { StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useSession } from '@/context/auth';
import { useTheme } from '@/hooks/use-theme';

export default function ProfileScreen() {
  const { me, signOut } = useSession();
  const theme = useTheme();

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <ThemedText type="title" style={styles.heading}>
          Profile
        </ThemedText>

        <ThemedView style={[styles.section, { backgroundColor: theme.backgroundElement }]}>
          <ThemedText type="smallBold">{me?.full_name}</ThemedText>
          {me?.role_display ? <ThemedText themeColor="textSecondary">{me.role_display}</ThemedText> : null}
          {me?.country ? <ThemedText themeColor="textSecondary">{me.country}</ThemedText> : null}
        </ThemedView>

        <TouchableOpacity style={[styles.button, { borderColor: theme.danger }]} onPress={signOut}>
          <ThemedText themeColor="danger">Sign out</ThemedText>
        </TouchableOpacity>
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1, paddingHorizontal: Spacing.three, gap: Spacing.three },
  heading: { fontSize: 22, paddingVertical: Spacing.three },
  section: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  button: {
    borderWidth: 1, borderRadius: Spacing.two, paddingVertical: Spacing.three, alignItems: 'center',
  },
});
