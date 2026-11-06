import { useLocalSearchParams, useRouter } from 'expo-router';
import { type ComponentRef, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { WebView, type WebViewNavigation } from 'react-native-webview';

import { API_BASE_URL, getToken } from '@/api/client';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

/**
 * A website page inside the app (More → a page, or a ticket's full page):
 * signed in through /api/website-session/ — the token goes once, in a
 * header — then it's simply the website, in the person's language, with
 * its own permission checks. "‹ Back" steps back through the pages
 * visited here before leaving.
 */
export default function WebsiteScreen() {
  const { path, title } = useLocalSearchParams<{ path: string; title?: string }>();
  const router = useRouter();
  const theme = useTheme();
  const webView = useRef<ComponentRef<typeof WebView>>(null);
  const [token, setToken] = useState<string | null>(null);
  const [canGoBack, setCanGoBack] = useState(false);

  useEffect(() => {
    getToken().then(setToken);
  }, []);

  const back = () => {
    if (canGoBack) {
      webView.current?.goBack();
    } else {
      router.back();
    }
  };

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <ThemedView style={styles.bar}>
          <TouchableOpacity onPress={back} style={styles.backButton}>
            <ThemedText themeColor="primary">‹ Back</ThemedText>
          </TouchableOpacity>
          <ThemedText type="smallBold" numberOfLines={1} style={styles.title}>{title ?? ''}</ThemedText>
          <TouchableOpacity onPress={() => router.back()} style={styles.backButton}>
            <ThemedText themeColor="textSecondary">Close</ThemedText>
          </TouchableOpacity>
        </ThemedView>
        {token ? (
          <WebView
            ref={webView}
            source={{
              uri: `${API_BASE_URL}/api/website-session/?next=${encodeURIComponent(path ?? '/')}`,
              headers: { Authorization: `Token ${token}` },
            }}
            onNavigationStateChange={(state: WebViewNavigation) => setCanGoBack(state.canGoBack)}
            startInLoadingState
            renderLoading={() => <ActivityIndicator style={styles.loading} color={theme.primary} />}
            sharedCookiesEnabled
            pullToRefreshEnabled
            style={styles.page}
          />
        ) : <ActivityIndicator style={styles.loading} color={theme.primary} />}
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  bar: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: Spacing.three, paddingVertical: Spacing.two, gap: Spacing.two,
  },
  backButton: { paddingVertical: Spacing.one },
  title: { flex: 1, textAlign: 'center' },
  page: { flex: 1 },
  loading: { position: 'absolute', top: '45%', alignSelf: 'center' },
});
