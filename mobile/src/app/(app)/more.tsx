import Ionicons from '@expo/vector-icons/Ionicons';
import { useFocusEffect, useRouter } from 'expo-router';
import { type ComponentProps, useCallback, useState } from 'react';
import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchWebsiteMenu } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { WebsiteMenuGroup } from '@/types';
import { CountryChooser } from '@/components/country-chooser';

type IconName = ComponentProps<typeof Ionicons>['name'];

/** Website pages the app has its own screen for: those open natively. */
const NATIVE: Record<string, string> = {
  '/tasks/': '/team',
  '/customers/': '/customers',
  '/tasks/machines/': '/machines',
  '/tasks/technicians/hours/': '/hours',
  '/tasks/tickets/internal/': '/internal-tickets',
  '/tasks/technicians/map/': '/team-map',
  '/tasks/technicians/progress/': '/team?view=progress',
  '/tasks/technicians/': '/team?view=progress',
};

/** The website menu's icon names, as the nearest Ionicons. */
const ICONS: Record<string, IconName> = {
  award: 'ribbon-outline', 'bar-chart': 'bar-chart-outline', bell: 'notifications-outline',
  building: 'business-outline', calendar: 'calendar-outline', 'calendar-range': 'calendar-number-outline',
  clipboard: 'clipboard-outline', clock: 'time-outline', dumbbell: 'barbell-outline',
  'file-clock': 'document-text-outline', globe: 'globe-outline', 'graduation-cap': 'school-outline',
  grid: 'grid-outline', 'id-card': 'id-card-outline', inbox: 'mail-unread-outline', key: 'key-outline',
  'map-pin': 'location-outline', package: 'cube-outline', shield: 'shield-checkmark-outline',
  tag: 'pricetag-outline', ticket: 'ticket-outline', 'ticket-plus': 'add-circle-outline',
  'trending-up': 'trending-up-outline', 'user-check': 'person-outline', users: 'people-outline',
};

/**
 * Everything else on the website, by the same menu (and permissions) as
 * its sidebar: tickets, tasks, customers, machines, the dashboard, hours,
 * reports and setup. Each opens the real website page inside the app.
 */
export default function MoreScreen() {
  const router = useRouter();
  const theme = useTheme();
  const [groups, setGroups] = useState<WebsiteMenuGroup[] | null>(null);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setGroups(await fetchWebsiteMenu());
      setError('');
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Could not load the menu.');
    }
  }, []);

  useFocusEffect(useCallback(() => {
    load();
  }, [load]));

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <ScrollView
          contentContainerStyle={styles.content}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => {
            setRefreshing(true);
            await load();
            setRefreshing(false);
          }} />}
        >
          <ThemedText type="title">More</ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            Everything else from the website, signed in as you.
          </ThemedText>
          <CountryChooser onChange={load} inset={false} />
          {error ? <ThemedText themeColor="danger">{error}</ThemedText> : null}
          {groups === null && !error ? <ActivityIndicator /> : null}
          {groups?.map((group, index) => (
            <ThemedView key={index} style={styles.group}>
              {group.heading ? <ThemedText type="smallBold" themeColor="textSecondary">{group.heading}</ThemedText> : null}
              {group.pages.map((page) => (
                <TouchableOpacity
                  key={page.url}
                  style={[styles.page, { backgroundColor: theme.backgroundElement }]}
                  onPress={() => (NATIVE[page.url]
                    ? router.push(NATIVE[page.url] as never)
                    : router.push({ pathname: '/website', params: { path: page.url, title: page.title } }))}
                >
                  <Ionicons name={ICONS[page.icon] ?? 'open-outline'} size={20} color={theme.primary} />
                  <ThemedText style={styles.pageTitle}>{page.title}</ThemedText>
                  <Ionicons name="chevron-forward" size={16} color={theme.textSecondary} />
                </TouchableOpacity>
              ))}
            </ThemedView>
          ))}
        </ScrollView>
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  content: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.six, gap: Spacing.three },
  group: { gap: Spacing.two, backgroundColor: 'transparent' },
  page: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.three,
    borderRadius: Spacing.two, padding: Spacing.three,
  },
  pageTitle: { flex: 1 },
});
