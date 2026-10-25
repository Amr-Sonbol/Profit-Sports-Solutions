import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { ActivityIndicator, Linking, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchCustomer } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { CustomerPage } from '@/types';

/**
 * A customer — the website's customer page: their contacts, each site
 * (address, contact to call, access notes, directions, its machines) and
 * their recent tasks. Editing the customer or a site is on the website
 * ("Edit on the website").
 */
export default function CustomerScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const theme = useTheme();
  const [customer, setCustomer] = useState<CustomerPage | null>(null);
  const [error, setError] = useState('');

  useFocusEffect(useCallback(() => {
    fetchCustomer(Number(id)).then(setCustomer).catch((err) => {
      setError(err instanceof ApiRequestError ? err.message : 'Could not load this customer.');
    });
  }, [id]));

  if (!customer) {
    return (
      <ThemedView style={styles.centered}>
        {error ? <ThemedText themeColor="danger">{error}</ThemedText> : <ActivityIndicator />}
      </ThemedView>
    );
  }

  const button = (icon: React.ComponentProps<typeof Ionicons>['name'], label: string, onPress: () => void) => (
    <TouchableOpacity style={[styles.button, { borderColor: theme.primary }]} onPress={onPress}>
      <Ionicons name={icon} size={16} color={theme.primary} />
      <ThemedText type="small" themeColor="primary" numberOfLines={1}>{label}</ThemedText>
    </TouchableOpacity>
  );

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <ScrollView contentContainerStyle={styles.content}>
          <TouchableOpacity onPress={() => router.back()}>
            <ThemedText themeColor="primary">← Back</ThemedText>
          </TouchableOpacity>
          <ThemedText type="title">{customer.name}</ThemedText>
          <ThemedText themeColor="textSecondary">{customer.segment}{customer.has_portal ? ' · has a portal login' : ''}</ThemedText>
          {customer.is_internal ? (
            <ThemedText type="small" themeColor="primary">
              Our own account: each site is one of our warehouses, where equipment is received and loaded.
            </ThemedText>
          ) : null}

          <ThemedView style={styles.row}>
            {customer.contact_phone ? button('call-outline', `Call ${customer.contact_name || customer.contact_phone}`,
              () => Linking.openURL(`tel:${customer.contact_phone}`)) : null}
            {customer.contact_email ? button('mail-outline', 'Email',
              () => Linking.openURL(`mailto:${customer.contact_email}`)) : null}
            {button('create-outline', 'Edit on the website', () => router.push({
              pathname: '/website', params: { path: `/customers/${customer.id}/edit/`, title: customer.name },
            }))}
          </ThemedView>
          {customer.billing_address ? (
            <ThemedText type="small" themeColor="textSecondary">Billing: {customer.billing_address}</ThemedText>
          ) : null}

          <ThemedText type="subtitle">Sites</ThemedText>
          {customer.sites.map((site) => (
            <ThemedView key={site.id} style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
              <ThemedText type="smallBold">{site.name}</ThemedText>
              <ThemedText type="small">{site.address}</ThemedText>
              {site.access_notes ? (
                <ThemedText type="small" themeColor="textSecondary">Access: {site.access_notes}</ThemedText>
              ) : null}
              <ThemedView style={styles.row}>
                {button('navigate-outline', 'Directions', () => Linking.openURL(site.directions_url))}
                {site.contact_phone ? button('call-outline', `Call ${site.contact_name || site.contact_phone}`,
                  () => Linking.openURL(`tel:${site.contact_phone}`)) : null}
              </ThemedView>
              {site.machines.length ? (
                <ThemedView style={styles.machines}>
                  <ThemedText type="small" themeColor="textSecondary">Machines ({site.machines.length})</ThemedText>
                  {site.machines.map((machine) => (
                    <TouchableOpacity
                      key={machine.id}
                      onPress={() => router.push({ pathname: '/machine/[id]', params: { id: String(machine.id) } })}
                    >
                      <ThemedText type="small" themeColor="primary">
                        {machine.name}{machine.serial ? ` · ${machine.serial}` : ''} ›
                      </ThemedText>
                    </TouchableOpacity>
                  ))}
                </ThemedView>
              ) : null}
            </ThemedView>
          ))}

          <ThemedText type="subtitle">Recent tasks</ThemedText>
          {customer.tasks.length ? customer.tasks.map((task) => (
            <TouchableOpacity
              key={task.id}
              style={[styles.card, { backgroundColor: theme.backgroundElement }]}
              onPress={() => router.push(`/team-task/${task.id}`)}
            >
              <ThemedText type="smallBold">{task.task_number} · {task.status_display}</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">
                {new Date(task.reported_at).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' })}
                {' · '}{task.site_name}{task.task_type_name ? ` · ${task.task_type_name}` : ''}
              </ThemedText>
            </TouchableOpacity>
          )) : <ThemedText themeColor="textSecondary">No tasks yet.</ThemedText>}
        </ScrollView>
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: Spacing.four },
  content: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.six, gap: Spacing.two },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  button: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.one, borderWidth: 1, borderRadius: Spacing.two,
    paddingHorizontal: Spacing.three, paddingVertical: Spacing.one,
  },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  machines: { gap: 2, marginTop: Spacing.one, backgroundColor: 'transparent' },
});
