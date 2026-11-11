import { Stack } from 'expo-router';

/**
 * The signed-in app: the main sections as tabs ((tabs)/_layout.tsx), and
 * every other screen — a task, a report, a customer, a machine… — opened
 * on top of them, with the back swipe. (A tab hidden with `href: null` is
 * no longer something to navigate to in this Expo Router: it sends you back.)
 * Grouping doesn't change any address: /task/12 is still /task/12.
 */
export default function AppLayout() {
  return <Stack screenOptions={{ headerShown: false }} />;
}
