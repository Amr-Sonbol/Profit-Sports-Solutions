import { useState } from 'react';
import { StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { CountryChooser } from '@/components/country-chooser';
import { TeamProgressList } from '@/components/team-progress';
import { TeamTodayList } from '@/components/team-today';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

/**
 * The people: Today — who's where right now (components/team-today) — and
 * Progress — how each one is doing this month, their skills to confirm. A tap
 * on someone opens their page. The country's tasks are on the Tasks tab.
 */
export default function TeamScreen() {
  const theme = useTheme();
  const [view, setView] = useState<'today' | 'progress'>('today');
  // Bumped when a manager switches country, so the lists reload.
  const [countryVersion, setCountryVersion] = useState(0);
  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <ThemedText type="title" style={styles.heading}>Team</ThemedText>
        <CountryChooser onChange={() => setCountryVersion((version) => version + 1)} />
        <ThemedView style={styles.switcher}>
          {(['today', 'progress'] as const).map((option) => (
            <TouchableOpacity
              key={option} onPress={() => setView(option)}
              style={[styles.switch, { borderColor: theme.backgroundSelected },
                view === option && { backgroundColor: theme.primary, borderColor: theme.primary }]}
            >
              <ThemedText type="small" style={view === option ? { color: theme.onPrimary } : undefined}>
                {option === 'today' ? 'Today' : 'Progress'}
              </ThemedText>
            </TouchableOpacity>
          ))}
        </ThemedView>
        {view === 'today' ? <TeamTodayList key={countryVersion} /> : <TeamProgressList key={countryVersion} />}
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  heading: { fontSize: 22, paddingHorizontal: Spacing.three, paddingVertical: Spacing.three },
  switcher: { flexDirection: 'row', gap: Spacing.two, paddingHorizontal: Spacing.three, paddingBottom: Spacing.two },
  switch: { flex: 1, borderWidth: 1, borderRadius: 999, paddingVertical: Spacing.two, alignItems: 'center' },
});
