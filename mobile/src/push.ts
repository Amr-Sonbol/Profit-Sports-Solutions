import Constants from 'expo-constants';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import { Platform } from 'react-native';

import { apiRequest } from '@/api/client';

/**
 * Push notifications (server side: people/push.py). The phone's Expo push
 * token is registered with the API after sign-in and dropped on sign-out.
 *
 * Needs the app linked to an Expo (EAS) project — the project id in
 * app.json under extra.eas.projectId, which `npx eas-cli@latest init`
 * adds. Until then, and in a simulator, this quietly does nothing.
 */

// Shown even while the app is open, so a new assignment isn't missed.
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

let registeredToken: string | null = null;

function projectId(): string | undefined {
  return Constants.expoConfig?.extra?.eas?.projectId ?? Constants.easConfig?.projectId;
}

export async function registerForPush(): Promise<void> {
  const id = projectId();
  if (!Device.isDevice || !id || Platform.OS === 'web') {
    return;
  }
  try {
    if (Platform.OS === 'android') {
      await Notifications.setNotificationChannelAsync('default', {
        name: 'Default',
        importance: Notifications.AndroidImportance.HIGH,
      });
    }
    let { status } = await Notifications.getPermissionsAsync();
    if (status !== 'granted') {
      ({ status } = await Notifications.requestPermissionsAsync());
    }
    if (status !== 'granted') {
      return;
    }
    const token = (await Notifications.getExpoPushTokenAsync({ projectId: id })).data;
    await apiRequest<void>('/api/push-device/', { method: 'POST', body: { token } });
    registeredToken = token;
  } catch {
    // No permission, no network, no Play services — the app works without push.
  }
}

/** Before signing out, so the next person on this phone doesn't get these. */
export async function unregisterForPush(): Promise<void> {
  if (!registeredToken) {
    return;
  }
  try {
    await apiRequest<void>('/api/push-device/', { method: 'DELETE', body: { token: registeredToken } });
  } catch {
    // Signing out anyway; the server drops tokens Expo reports as gone.
  }
  registeredToken = null;
}

/** Where tapping a notification should take you (data set in people/push.py). */
export function routeForNotification(data: Record<string, unknown>, role: string | null): string | null {
  const taskId = data.task_id;
  switch (data.type) {
    case 'my_task':
      return `/task/${taskId}`;
    case 'team_task':
      return `/team-task/${taskId}`;
    case 'task_message':
      return role === 'technician' ? `/task/${taskId}` : `/team-task/${taskId}`;
    case 'ticket':
      return '/tickets';
    default:
      return null;
  }
}
