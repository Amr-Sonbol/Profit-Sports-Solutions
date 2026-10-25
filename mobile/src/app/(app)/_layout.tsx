import { useEffect, useRef } from 'react';
import Ionicons from '@expo/vector-icons/Ionicons';
import { Tabs, useRouter } from 'expo-router';
import type { ComponentProps } from 'react';
import { type ColorValue, Platform } from 'react-native';

import { useSession } from '@/context/auth';
import { useTheme } from '@/hooks/use-theme';

type IconName = ComponentProps<typeof Ionicons>['name'];

type TabIconProps = { color: ColorValue; focused: boolean; size: number };

/** A tab's icon: outlined, filled when it's the open tab. */
function TabIcon({ name, color, focused, size }: TabIconProps & { name: string }) {
  return <Ionicons name={(focused ? name : `${name}-outline`) as IconName} color={color as string} size={size} />;
}

function tabIcon(name: string) {
  return function Icon(props: TabIconProps) {
    return <TabIcon name={name} {...props} />;
  };
}

/**
 * The tabs this person can use: Team needs the task list or the team
 * (view_tasks / view_technicians), Tickets needs manage_tickets — the
 * same permissions the website's sidebar follows, sent with /api/me/.
 * The API still checks every request; this just doesn't offer a tab that
 * would only say "no permission".
 */
export default function AppTabsLayout() {
  const theme = useTheme();
  const { me } = useSession();
  const can = (permission: string) => me?.permissions?.includes(permission) ?? false;
  const showTeam = can('view_tasks') || can('view_technicians');
  const showTickets = can('manage_tickets');
  // A technician's website pages are all their own (schedule, progress,
  // skills, profile) — tabs here already — so More would be empty.
  const showMore = Boolean(me?.role) && me?.role !== 'technician';
  // A manager's home screen; they have no My progress (field staff's), as on the website.
  const showDashboard = can('view_dashboard');
  const isManagerTier = me?.role === 'manager' || me?.role === 'admin';
  const router = useRouter();
  const openedDashboard = useRef(false);
  useEffect(() => {
    // Open on the Dashboard once, for whoever has one; Schedule stays a tab.
    if (showDashboard && !openedDashboard.current) {
      openedDashboard.current = true;
      router.navigate('/dashboard');
    }
  }, [showDashboard, router]);

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
      <Tabs.Screen
        name="dashboard"
        options={{ title: 'Dashboard', tabBarIcon: tabIcon('speedometer'), href: showDashboard ? undefined : null }}
      />
      <Tabs.Screen name="index" options={{ title: 'Schedule', tabBarIcon: tabIcon('calendar') }} />
      <Tabs.Screen
        name="progress"
        options={{ title: 'Progress', tabBarIcon: tabIcon('trending-up'), href: isManagerTier ? null : undefined }}
      />
      <Tabs.Screen
        name="team" options={{ title: 'Team', tabBarIcon: tabIcon('people'), href: showTeam ? undefined : null }}
      />
      <Tabs.Screen
        name="tickets"
        options={{ title: 'Tickets', tabBarIcon: tabIcon('ticket'), href: showTickets ? undefined : null }}
      />
      <Tabs.Screen name="profile" options={{ title: 'Profile', tabBarIcon: tabIcon('person-circle') }} />
      <Tabs.Screen
        name="more" options={{ title: 'More', tabBarIcon: tabIcon('grid'), href: showMore ? undefined : null }}
      />
      <Tabs.Screen name="task/[id]" options={{ href: null }} />
      <Tabs.Screen name="report/[id]" options={{ href: null }} />
      <Tabs.Screen name="team-task/[id]" options={{ href: null }} />
      <Tabs.Screen name="schedule/[id]" options={{ href: null }} />
      <Tabs.Screen name="change-password" options={{ href: null }} />
      <Tabs.Screen name="skills" options={{ href: null }} />
      <Tabs.Screen name="edit-profile" options={{ href: null }} />
      <Tabs.Screen name="people/[id]" options={{ href: null }} />
      <Tabs.Screen name="website" options={{ href: null }} />
      <Tabs.Screen name="new-task" options={{ href: null }} />
      <Tabs.Screen name="customers" options={{ href: null }} />
      <Tabs.Screen name="customer/[id]" options={{ href: null }} />
      <Tabs.Screen name="machines" options={{ href: null }} />
      <Tabs.Screen name="machine/[id]" options={{ href: null }} />
      <Tabs.Screen name="hours" options={{ href: null }} />
      <Tabs.Screen name="internal-tickets" options={{ href: null }} />
      <Tabs.Screen name="team-map" options={{ href: null }} />
    </Tabs>
  );
}
