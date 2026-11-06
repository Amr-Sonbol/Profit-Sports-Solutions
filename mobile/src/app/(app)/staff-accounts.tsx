import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { Alert, FlatList, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchStaffAccounts, staffAccountAction } from '@/api/endpoints';
import { SearchBox } from '@/components/search-box';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import { useTheme } from '@/hooks/use-theme';
import type { StaffAccount } from '@/types';

/**
 * More → Staff accounts (manager tier): the urgent fixes, from the phone —
 * a lost phone (sign out of every phone), a locked-out or forgotten
 * password (unlock, temporary password read out once), someone who must
 * not get in (turn the login off / on). Everything is in the audit log.
 * Setup links and email changes are on the website ("Full page").
 */
export default function StaffAccountsScreen() {
  const router = useRouter();
  const theme = useTheme();
  const [rows, setRows] = useState<StaffAccount[] | null>(null);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  // The person whose fixes are showing (Android alerts hold three buttons, so they're inline).
  const [open, setOpen] = useState<number | null>(null);
  const query = useDebouncedValue(search.trim());

  const load = useCallback(async () => {
    try {
      setRows(await fetchStaffAccounts(query));
      setError('');
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Could not load the accounts.');
    }
  }, [query]);

  useFocusEffect(useCallback(() => {
    load();
  }, [load]));

  const act = async (person: StaffAccount, action: string) => {
    try {
      const result = await staffAccountAction(person.id, action);
      if (result.password) {
        Alert.alert(
          `Temporary password for ${person.name}`,
          `${result.password}\n\nRead it out to them now — it isn't shown again. They choose their own at their next sign-in, and they've been signed out of the app on every phone.`,
        );
      } else {
        Alert.alert('Done', result.message);
      }
      load();
    } catch (err) {
      Alert.alert('Not done', err instanceof ApiRequestError ? err.message : 'Please try again.');
    }
  };

  const confirm = (person: StaffAccount, action: string, question: string) => {
    Alert.alert(person.name, question, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Yes', style: action === 'disable' ? 'destructive' : 'default', onPress: () => act(person, action) },
    ]);
  };

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <TouchableOpacity onPress={() => router.back()} style={styles.back}>
          <ThemedText themeColor="primary">← Back</ThemedText>
        </TouchableOpacity>
        <ThemedView style={styles.headerRow}>
          <ThemedText type="title" style={styles.heading}>Staff accounts</ThemedText>
          <TouchableOpacity
            onPress={() => router.push({ pathname: '/website', params: { path: '/tasks/technicians/accounts/', title: 'Staff accounts' } })}
          >
            <ThemedText type="small" themeColor="primary">Full page</ThemedText>
          </TouchableOpacity>
        </ThemedView>
        <SearchBox value={search} onChangeText={setSearch} placeholder="Name, username or email" />
        {error ? <ThemedText themeColor="danger" style={styles.message}>{error}</ThemedText> : null}
        <FlatList
          data={rows ?? []}
          keyExtractor={(row) => String(row.id)}
          contentContainerStyle={styles.list}
          ListEmptyComponent={rows !== null ? <ThemedText themeColor="textSecondary">No one matches.</ThemedText> : undefined}
          renderItem={({ item }) => (
            <TouchableOpacity
              style={[styles.card, { backgroundColor: theme.backgroundElement }]}
              disabled={!item.can_manage}
              onPress={() => setOpen(open === item.id ? null : item.id)}
            >
              <ThemedText type="smallBold">{item.name}</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                {item.role}{item.username ? ` · ${item.username}` : ' · no login'}
              </ThemedText>
              <ThemedText type="small" themeColor={item.status === 'active' ? 'textSecondary' : 'danger'}>
                {item.status_display}
              </ThemedText>
              {item.can_manage && open !== item.id ? <ThemedText type="small" themeColor="primary">Fix ›</ThemedText> : null}
              {open === item.id ? (
                <ThemedView style={styles.actions}>
                  {[
                    ['sign_out_phones', 'Sign out of every phone', 'Sign them out of the app on every phone (for a lost phone)?'],
                    ['unlock', 'Unlock', ''],
                    ...(item.is_active ? [
                      ['temporary_password', 'Temporary password', 'Set a temporary password to read out to them? They will be signed out of the app.'],
                      ['disable', 'Turn login off', 'Turn their login off? They cannot sign in until it is turned on again.'],
                    ] : [['enable', 'Turn login on', '']]),
                  ].map(([action, label, question]) => (
                    <TouchableOpacity
                      key={action}
                      style={[styles.actionButton, { borderColor: action === 'disable' ? theme.danger : theme.primary }]}
                      onPress={() => (question ? confirm(item, action, question) : act(item, action))}
                    >
                      <ThemedText type="small" style={{ color: action === 'disable' ? theme.danger : theme.primary }}>{label}</ThemedText>
                    </TouchableOpacity>
                  ))}
                </ThemedView>
              ) : null}
            </TouchableOpacity>
          )}
        />
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  back: { paddingHorizontal: Spacing.three, paddingTop: Spacing.two },
  headerRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingEnd: Spacing.three, backgroundColor: 'transparent',
  },
  heading: { paddingHorizontal: Spacing.three, paddingVertical: Spacing.two },
  message: { paddingHorizontal: Spacing.three },
  list: { padding: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: 2 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, marginTop: Spacing.two, backgroundColor: 'transparent' },
  actionButton: { borderWidth: 1, borderRadius: Spacing.two, paddingHorizontal: Spacing.three, paddingVertical: Spacing.one },
});
