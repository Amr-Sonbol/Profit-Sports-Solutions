import { Tabs } from 'expo-router';
import { Platform } from 'react-native';

import { useTheme } from '@/hooks/use-theme';

/**
 * Every tab is reachable regardless of role — access itself is enforced
 * by the API (same RolePermission checks as the web app; see
 * api/permissions.py), not by hiding tabs client-side. A technician with
 * no team-management permissions just sees an empty/blocked state on the
 * Team or Tickets tab rather than the tab disappearing, which is simpler
 * than fetching "what am I allowed to see" separately just to decide
 * what to render.
 */
export default function AppTabsLayout() {
  const theme = useTheme();

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: theme.primary,
        tabBarInactiveTintColor: theme.textSecondary,
        tabBarStyle: Platform.select({
          ios: { position: 'absolute' },
          default: {},
        }),
      }}
    >
      <Tabs.Screen name="index" options={{ title: 'My Tasks' }} />
      <Tabs.Screen name="team" options={{ title: 'Team' }} />
      <Tabs.Screen name="tickets" options={{ title: 'Tickets' }} />
      <Tabs.Screen name="profile" options={{ title: 'Profile' }} />
      <Tabs.Screen name="task/[id]" options={{ href: null }} />
      <Tabs.Screen name="report/[id]" options={{ href: null }} />
      <Tabs.Screen name="team-task/[id]" options={{ href: null }} />
    </Tabs>
  );
}
