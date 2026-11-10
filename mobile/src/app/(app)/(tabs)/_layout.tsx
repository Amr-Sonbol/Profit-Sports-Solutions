import { useEffect, useRef } from 'react';
import Ionicons from '@expo/vector-icons/Ionicons';
import { Tabs, useRouter } from 'expo-router';
import type { ComponentProps } from 'react';
import { type ColorValue } from 'react-native';

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
  // The people: their progress, skills and pages. (Tasks are on the Tasks tab.)
  const showTeam = can('view_technicians');
  const showTickets = can('manage_tickets');
  // A technician's website pages are all their own (schedule, progress,
  // skills, profile) — tabs here already — so More would be empty.
  const showMore = Boolean(me?.role) && me?.role !== 'technician';
  // A manager's home screen; they have no My progress (field staff's), as on the website.
  const showDashboard = can('view_dashboard');
  // Their own month and skills: the field team's (the server says, as the website's My progress).
  const showProgress = me?.field_staff ?? (me?.role === 'technician' || me?.role === 'supervisor');
  // Who plans others' work (a supervisor) opens on Tasks, where "To plan" is.
  const plansWork = can('assign_tasks');
  const router = useRouter();
  const opened = useRef(false);
  useEffect(() => {
    // The first screen, once: a manager's Dashboard; a supervisor's Tasks; a technician's
    // Schedule (the next job and its Directions) — the tabs stay as they are.
    if (opened.current || !me) {
      return;
    }
    opened.current = true;
    if (showDashboard) {
      router.navigate('/dashboard');
    } else if (plansWork) {
      router.navigate('/tasks');
    }
  }, [me, showDashboard, plansWork, router]);

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: theme.primary,
        tabBarInactiveTintColor: theme.textSecondary,
        // Not floating over the screen (as iOS tab bars can): content near the bottom
        // stayed underneath it and couldn't be tapped (a job's "Open task").
        tabBarStyle: {},
      }}
    >
      <Tabs.Screen
        name="dashboard"
        options={{ title: 'Dashboard', tabBarIcon: tabIcon('speedometer'), href: showDashboard ? undefined : null }}
      />
      <Tabs.Screen name="tasks" options={{ title: 'Tasks', tabBarIcon: tabIcon('list') }} />
      <Tabs.Screen name="index" options={{ title: 'Schedule', tabBarIcon: tabIcon('calendar') }} />
      <Tabs.Screen
        name="progress"
        options={{ title: 'Progress', tabBarIcon: tabIcon('trending-up'), href: showProgress ? undefined : null }}
      />
      <Tabs.Screen
        name="team" options={{ title: 'Team', tabBarIcon: tabIcon('people'), href: showTeam ? undefined : null }}
      />
      <Tabs.Screen
        name="tickets"
        options={{ title: 'Tickets', tabBarIcon: tabIcon('ticket'), href: showTickets ? undefined : null }}
      />
      <Tabs.Screen
        name="more" options={{ title: 'More', tabBarIcon: tabIcon('grid'), href: showMore ? undefined : null }}
      />
      {/* Always the last tab (far right; far left in Arabic). */}
      <Tabs.Screen name="profile" options={{ title: 'Profile', tabBarIcon: tabIcon('person-circle') }} />
    </Tabs>
  );
}
