import Ionicons from '@expo/vector-icons/Ionicons';
import { Image } from 'expo-image';
import { useRouter } from 'expo-router';
import type { ComponentProps } from 'react';
import { ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useSession } from '@/context/auth';
import { useTheme } from '@/hooks/use-theme';

type IconName = ComponentProps<typeof Ionicons>['name'];

/** "Tarek Mansour" -> "TM", for someone with no photo yet. */
function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]!.toUpperCase()).join('');
}

/**
 * Who's signed in: their photo (Edit profile, or the website's My profile) or
 * initials, name and role, then their details and account actions.
 */
export default function ProfileScreen() {
  const { me, signOut } = useSession();
  const theme = useTheme();
  const router = useRouter();

  const detail = (icon: IconName, label: string, value?: string) => (value ? (
    <ThemedView style={styles.detail}>
      <Ionicons name={icon} size={20} color={theme.textSecondary} />
      <ThemedView style={styles.detailText}>
        <ThemedText type="small" themeColor="textSecondary">{label}</ThemedText>
        <ThemedText>{value}</ThemedText>
      </ThemedView>
    </ThemedView>
  ) : null);

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <ScrollView contentContainerStyle={styles.content}>
          <ThemedView style={styles.header}>
            {me?.photo_url ? (
              <Image source={{ uri: me.photo_url }} style={styles.photo} contentFit="cover" accessibilityLabel={me.full_name} />
            ) : (
              <ThemedView style={[styles.photo, styles.initials, { backgroundColor: theme.primary }]}>
                <ThemedText style={[styles.initialsText, { color: theme.onPrimary }]}>{initials(me?.full_name ?? '')}</ThemedText>
              </ThemedView>
            )}
            <ThemedText type="subtitle" style={styles.name}>{me?.full_name}</ThemedText>
            {me?.role_display ? (
              <ThemedView style={[styles.badge, { borderColor: theme.text }]}>
                <ThemedText type="smallBold">{me.role_display}</ThemedText>
              </ThemedView>
            ) : null}
          </ThemedView>

          <ThemedView style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
            {detail('globe-outline', 'Country', me?.country)}
            {detail('briefcase-outline', 'Employment', me?.employment_type_display)}
            {detail('at-outline', 'Username', me?.username)}
            {detail('mail-outline', 'Email', me?.email)}
            {detail('call-outline', 'Phone', me?.phone)}
          </ThemedView>
          <TouchableOpacity
            style={[styles.button, { borderColor: theme.backgroundSelected }]}
            onPress={() => router.push('/edit-profile')}
          >
            <Ionicons name="create-outline" size={18} color={theme.text} />
            <ThemedText>{me?.photo_url ? 'Edit profile' : 'Add your photo and edit profile'}</ThemedText>
          </TouchableOpacity>

          <TouchableOpacity
            style={[styles.button, { borderColor: theme.backgroundSelected }]}
            onPress={() => router.push('/skills')}
          >
            <Ionicons name="ribbon-outline" size={18} color={theme.text} />
            <ThemedText>My skills</ThemedText>
          </TouchableOpacity>

          <TouchableOpacity
            style={[styles.button, { borderColor: theme.backgroundSelected }]}
            onPress={() => router.push('/change-password')}
          >
            <Ionicons name="key-outline" size={18} color={theme.text} />
            <ThemedText>Change password</ThemedText>
          </TouchableOpacity>

          <TouchableOpacity style={[styles.button, { borderColor: theme.danger }]} onPress={signOut}>
            <Ionicons name="log-out-outline" size={18} color={theme.danger} />
            <ThemedText themeColor="danger">Sign out</ThemedText>
          </TouchableOpacity>
        </ScrollView>
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  content: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.six, gap: Spacing.three },
  header: { alignItems: 'center', gap: Spacing.two, paddingTop: Spacing.four, backgroundColor: 'transparent' },
  photo: { width: 96, height: 96, borderRadius: 48 },
  initials: { alignItems: 'center', justifyContent: 'center' },
  initialsText: { fontSize: 34, fontWeight: '700' },
  name: { textAlign: 'center' },
  badge: { borderWidth: 1, borderRadius: 999, paddingHorizontal: Spacing.three, paddingVertical: 2 },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.three },
  detail: { flexDirection: 'row', alignItems: 'center', gap: Spacing.three, backgroundColor: 'transparent' },
  detailText: { flex: 1, backgroundColor: 'transparent' },
  button: {
    flexDirection: 'row', justifyContent: 'center', gap: Spacing.two,
    borderWidth: 1, borderRadius: Spacing.two, paddingVertical: Spacing.three, alignItems: 'center',
  },
});
