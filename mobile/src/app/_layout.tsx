import { DarkTheme, DefaultTheme, Stack, ThemeProvider, useRouter } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import * as Notifications from 'expo-notifications';
import { useEffect } from 'react';
import { useColorScheme } from 'react-native';

import { AuthProvider, useSession } from '@/context/auth';
import { routeForNotification } from '@/push';

SplashScreen.preventAutoHideAsync();

function RootNavigator() {
  const { me, isLoading } = useSession();
  const router = useRouter();

  // Tapping a push notification opens the task it's about.
  useEffect(() => {
    if (!me) {
      return;
    }
    const subscription = Notifications.addNotificationResponseReceivedListener((response) => {
      const route = routeForNotification(response.notification.request.content.data ?? {}, me.role);
      if (route) {
        router.push(route as never);
      }
    });
    return () => subscription.remove();
  }, [me, router]);

  useEffect(() => {
    if (!isLoading) {
      SplashScreen.hideAsync();
    }
  }, [isLoading]);

  if (isLoading) {
    // Still splash-screened at this point (hideAsync above hasn't fired
    // yet) — nothing needs to render.
    return null;
  }

  return (
    <Stack screenOptions={{ headerShown: false }}>
      <Stack.Protected guard={!!me}>
        <Stack.Screen name="(app)" />
      </Stack.Protected>
      <Stack.Protected guard={!me}>
        <Stack.Screen name="sign-in" />
      </Stack.Protected>
    </Stack>
  );
}

export default function RootLayout() {
  const colorScheme = useColorScheme();

  return (
    <ThemeProvider value={colorScheme === 'dark' ? DarkTheme : DefaultTheme}>
      <AuthProvider>
        <RootNavigator />
      </AuthProvider>
    </ThemeProvider>
  );
}
