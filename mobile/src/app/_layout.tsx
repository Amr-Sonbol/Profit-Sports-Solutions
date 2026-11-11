import { DarkTheme, DefaultTheme, type ErrorBoundaryProps, LocaleProvider, Stack, ThemeProvider, useRouter } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import * as Notifications from 'expo-notifications';
import { useEffect, useRef } from 'react';
import { Pressable, StyleSheet, Text, useColorScheme, View } from 'react-native';

import { AuthProvider, useSession } from '@/context/auth';
import { installErrorReporting, reportError } from '@/error-reporting';
import { isRTL, language, setLanguage, t } from '@/i18n';
import { routeForNotification } from '@/push';

SplashScreen.preventAutoHideAsync();
installErrorReporting();

function RootNavigator() {
  const { me, isLoading } = useSession();
  const router = useRouter();
  // The app's language comes with the signed-in person (their country's); it's
  // kept for the sign-in screen next time. Set before anything below renders.
  if (me?.app_language && me.app_language !== language()) {
    setLanguage(me.app_language);
  }

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

  // Right to left for Arabic, without a reload (works in Expo Go too): the
  // layout direction for the screens, LocaleProvider for the navigation's own.
  const direction = isRTL() ? 'rtl' : 'ltr';
  return (
    <View key={language()} style={[rootStyles.frame, { direction }]}>
    <LocaleProvider direction={direction}>
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
    </LocaleProvider>
    </View>
  );
}

const rootStyles = StyleSheet.create({
  frame: { flex: 1 },
});

/**
 * A screen that crashed: told to the office (error-reporting), and a plain
 * way back instead of a blank or red screen. Plain components only — the
 * app's own may be what failed.
 */
export function ErrorBoundary({ error, retry }: ErrorBoundaryProps) {
  const reported = useRef(false);
  useEffect(() => {
    if (!reported.current) {
      reported.current = true;
      reportError(error, 'screen');
    }
  }, [error]);
  return (
    <View style={errorStyles.page}>
      <Text style={errorStyles.title}>{t('Something went wrong on this screen')}</Text>
      <Text style={errorStyles.text}>{t('The office has been told. Try again; if it keeps happening, call your supervisor.')}</Text>
      <Pressable style={errorStyles.button} onPress={retry}>
        <Text style={errorStyles.buttonText}>{t('Try again')}</Text>
      </Pressable>
    </View>
  );
}

const errorStyles = StyleSheet.create({
  page: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, gap: 12, backgroundColor: '#FFFFFF' },
  title: { fontSize: 18, fontWeight: '700', color: '#111827', textAlign: 'center' },
  text: { fontSize: 15, color: '#4B5563', textAlign: 'center' },
  button: { backgroundColor: '#111827', borderRadius: 8, paddingHorizontal: 24, paddingVertical: 12 },
  buttonText: { color: '#FFFFFF', fontWeight: '600' },
});

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
