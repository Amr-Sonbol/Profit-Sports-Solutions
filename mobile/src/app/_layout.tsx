import { DarkTheme, DefaultTheme, Stack, ThemeProvider, useRouter } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import * as Notifications from 'expo-notifications';
import { useEffect, useRef } from 'react';
import { useColorScheme } from 'react-native';

import { AuthProvider, useSession } from '@/context/auth';
import { routeForNotification } from '@/push';

SplashScreen.preventAutoHideAsync();

function RootNavigator() {
  const { me, isLoading } = useSession();
  const router = useRouter();

  // Tapping a notification opens what it's about — with the app open, in the
  // background, or closed (the tap that starts it is kept until signed in).
  const tapped = Notifications.useLastNotificationResponse();
  const handled = useRef<string | null>(null);
  useEffect(() => {
    if (!me || !tapped || tapped.actionIdentifier !== Notifications.DEFAULT_ACTION_IDENTIFIER) {
      return;
    }
    const id = tapped.notification.request.identifier;
    if (handled.current === id) {
      return;
    }
    handled.current = id;
    router.push(routeForNotification(tapped.notification.request.content.data ?? {}, me.role) as never);
    Notifications.clearLastNotificationResponseAsync();
  }, [tapped, me, router]);

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
      {/* On a temporary password, only the screen to choose their own. */}
      <Stack.Protected guard={!!me && !me.must_change_password}>
        <Stack.Screen name="(app)" />
      </Stack.Protected>
      <Stack.Protected guard={!!me?.must_change_password}>
        <Stack.Screen name="choose-password" />
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
