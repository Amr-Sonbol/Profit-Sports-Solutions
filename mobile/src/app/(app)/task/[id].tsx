import { useCallback, useState } from 'react';
import * as ImagePicker from 'expo-image-picker';
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import {
  ActivityIndicator, Alert, ScrollView, StyleSheet, TouchableOpacity,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchMyTaskDetail, sendTaskAction, uploadTaskAttachment } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { withOfflineCopy } from '@/offline';
import type { TaskDetail } from '@/types';

const ACTION_LABELS: Record<string, string> = {
  accept: 'Accept task',
  en_route: 'On my way',
  arrive: 'I have arrived',
  start: 'Start work',
};

export default function TaskDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const theme = useTheme();
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState('');
  const [isOffline, setIsOffline] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data, offline } = await withOfflineCopy(`task-${id}`, () => fetchMyTaskDetail(Number(id)));
      setTask(data);
      setIsOffline(offline);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Could not load this task.');
    }
  }, [id]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const handleAction = async (action: string) => {
    setIsBusy(true);
    setError('');
    try {
      await sendTaskAction(Number(id), action);
      await load();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'That action failed — try again.');
    } finally {
      setIsBusy(false);
    }
  };

  const handleAddPhoto = async (purpose: 'fault' | 'serial_plate' | 'before' | 'after') => {
    const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) {
      Alert.alert('Permission needed', 'Photo library access is needed to attach a photo.');
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images', 'videos'],
      quality: 0.8,
    });
    if (result.canceled || !result.assets[0]) {
      return;
    }
    const asset = result.assets[0];
    setIsBusy(true);
    setError('');
    try {
      await uploadTaskAttachment(
        Number(id), asset.uri, asset.fileName ?? 'upload.jpg', asset.mimeType ?? 'image/jpeg', purpose,
      );
      await load();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Upload failed — try again.');
    } finally {
      setIsBusy(false);
    }
  };

  if (!task) {
    return (
      <ThemedView style={styles.centered}>
        {error ? <ThemedText themeColor="danger">{error}</ThemedText> : <ActivityIndicator />}
      </ThemedView>
    );
  }

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backLink}>
          <ThemedText themeColor="primary">← Back</ThemedText>
        </TouchableOpacity>

        <ScrollView contentContainerStyle={styles.content}>
          <ThemedText type="title" style={styles.taskNumber}>
            {task.task_number}
          </ThemedText>
          <ThemedText type="subtitle" style={styles.customer}>
            {task.customer_name}
          </ThemedText>
          <ThemedText themeColor="textSecondary">{task.site_name} — {task.site_address}</ThemedText>
          {isOffline ? (
            <ThemedText themeColor="danger" type="small">
              No connection — showing what was loaded last. Status buttons need a connection; the report can be
              filed offline.
            </ThemedText>
          ) : null}

          <ThemedView style={[styles.section, { backgroundColor: theme.backgroundElement }]}>
            <Row label="Status" value={task.status_display} />
            <Row label="Priority" value={task.priority_display} />
            {task.task_type_name ? <Row label="Type" value={task.task_type_name} /> : null}
            {task.brand_name ? <Row label="Brand" value={task.brand_name} /> : null}
            {task.required_skill_name ? <Row label="Skill needed" value={task.required_skill_name} /> : null}
          </ThemedView>

          {task.description ? (
            <ThemedView style={styles.section}>
              <ThemedText type="smallBold">Description</ThemedText>
              <ThemedText>{task.description}</ThemedText>
            </ThemedView>
          ) : null}

          {error ? (
            <ThemedText themeColor="danger" style={styles.error}>
              {error}
            </ThemedText>
          ) : null}

          {task.is_lead && task.next_action && ACTION_LABELS[task.next_action] ? (
            <TouchableOpacity
              style={[styles.button, { backgroundColor: theme.primary }, isBusy && styles.buttonDisabled]}
              onPress={() => handleAction(task.next_action!)}
              disabled={isBusy}
            >
              {isBusy ? <ActivityIndicator color="#fff" /> : (
                <ThemedText style={styles.buttonText}>{ACTION_LABELS[task.next_action]}</ThemedText>
              )}
            </TouchableOpacity>
          ) : null}

          {task.is_lead && task.undoable_step ? (
            <TouchableOpacity
              style={[styles.undoButton, { borderColor: theme.primary }, isBusy && styles.buttonDisabled]}
              onPress={() => handleAction('undo')}
              disabled={isBusy}
            >
              <ThemedText themeColor="primary">Undo “{task.undoable_step}”</ThemedText>
              <ThemedText themeColor="textSecondary" type="small">Tapped by mistake? Undo within 10 minutes.</ThemedText>
            </TouchableOpacity>
          ) : null}

          {task.can_file_report ? (
            <TouchableOpacity
              style={[styles.button, { backgroundColor: theme.primary }]}
              onPress={() => router.push(`/report/${task.id}`)}
            >
              <ThemedText style={styles.buttonText}>
                {task.status === 'in_progress' ? 'File work report' : 'Edit work report'}
              </ThemedText>
            </TouchableOpacity>
          ) : null}

          <ThemedView style={styles.section}>
            <ThemedText type="smallBold">Add a photo</ThemedText>
            <ThemedView style={styles.photoRow}>
              {(['fault', 'serial_plate', 'before', 'after'] as const).map((purpose) => (
                <TouchableOpacity
                  key={purpose}
                  style={[styles.photoButton, { borderColor: theme.backgroundSelected }]}
                  onPress={() => handleAddPhoto(purpose)}
                  disabled={isBusy}
                >
                  <ThemedText type="small">{purpose.replace('_', ' ')}</ThemedText>
                </TouchableOpacity>
              ))}
            </ThemedView>
          </ThemedView>

          {task.attachments.length > 0 ? (
            <ThemedView style={styles.section}>
              <ThemedText type="smallBold">Attachments ({task.attachments.length})</ThemedText>
              {task.attachments.map((attachment) => (
                <ThemedText key={attachment.id} themeColor="textSecondary" type="small">
                  {attachment.purpose_display} — {attachment.media_type_display}
                </ThemedText>
              ))}
            </ThemedView>
          ) : null}

          {task.events.length > 0 ? (
            <ThemedView style={styles.section}>
              <ThemedText type="smallBold">History</ThemedText>
              {task.events.map((event) => (
                <ThemedText key={event.id} themeColor="textSecondary" type="small">
                  {event.event_type_display} — {event.actor_name}
                </ThemedText>
              ))}
            </ThemedView>
          ) : null}
        </ScrollView>
      </SafeAreaView>
    </ThemedView>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <ThemedView style={styles.detailRow}>
      <ThemedText themeColor="textSecondary" type="small">{label}</ThemedText>
      <ThemedText type="small">{value}</ThemedText>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  backLink: { paddingHorizontal: Spacing.three, paddingVertical: Spacing.two },
  content: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.six, gap: Spacing.three },
  taskNumber: { fontSize: 24, marginBottom: 0 },
  customer: { fontSize: 18, marginBottom: 0 },
  section: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  detailRow: {
    flexDirection: 'row', justifyContent: 'space-between', backgroundColor: 'transparent',
  },
  error: { textAlign: 'center' },
  button: { borderRadius: Spacing.two, paddingVertical: Spacing.three, alignItems: 'center' },
  buttonDisabled: { opacity: 0.6 },
  undoButton: { borderWidth: 1, borderRadius: Spacing.two, paddingVertical: Spacing.two, alignItems: 'center' },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '600' },
  photoRow: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  photoButton: { borderWidth: 1, borderRadius: Spacing.two, paddingHorizontal: Spacing.two, paddingVertical: Spacing.two },
});
