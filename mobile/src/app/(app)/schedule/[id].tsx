import { useState } from 'react';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { type ScheduleMode, ScheduleView } from '@/components/schedule-view';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';

/**
 * Someone on the viewer's team: their week, month or year — the website's
 * "View schedule". Opened from Team → Progress; the API decides who may be
 * opened (the people this viewer oversees).
 */
export default function TeamMemberScheduleScreen() {
  const { id, name } = useLocalSearchParams<{ id: string; name?: string }>();
  const router = useRouter();
  const [mode, setMode] = useState<ScheduleMode>('week');

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backLink}>
          <ThemedText themeColor="primary">← Back</ThemedText>
        </TouchableOpacity>
        <ThemedText type="title" style={styles.heading}>{name ?? 'Schedule'}</ThemedText>
        <ScheduleView
          mode={mode}
          onModeChange={setMode}
          technicianId={Number(id)}
          taskRoute={(taskId) => `/team-task/${taskId}`}
        />
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  backLink: { paddingHorizontal: Spacing.three, paddingVertical: Spacing.two },
  heading: { fontSize: 22, paddingHorizontal: Spacing.three, paddingBottom: Spacing.three },
});
