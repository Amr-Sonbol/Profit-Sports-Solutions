import Ionicons from '@expo/vector-icons/Ionicons';
import { useCallback, useState, type ComponentProps } from 'react';
import * as ImagePicker from 'expo-image-picker';
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import {
  ActivityIndicator, Alert, Linking, ScrollView, StyleSheet, TextInput, TouchableOpacity,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchMyTaskDetail, markShipped, sendTaskAction, uploadTaskAttachment } from '@/api/endpoints';
import { CustomerRequest } from '@/components/customer-request';
import { TaskMessages } from '@/components/task-messages';
import { confirmAction, confirmStep } from '@/confirm';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useSession } from '@/context/auth';
import { useTheme } from '@/hooks/use-theme';
import { withOfflineCopy } from '@/offline';
import type { TaskDetail } from '@/types';
import { when } from '@/task-time';

const ACTION_LABELS: Record<string, string> = {
  accept: 'Confirm task',
  en_route: 'Heading to site',
  arrive: 'I have arrived',
  start: 'Start work',
};

type IconName = ComponentProps<typeof Ionicons>['name'];

// The four kinds of photo, each with its own icon.
const PHOTO_KINDS: { purpose: 'fault' | 'serial_plate' | 'before' | 'after'; label: string; icon: IconName }[] = [
  { purpose: 'fault', label: 'Fault', icon: 'warning-outline' },
  { purpose: 'serial_plate', label: 'Serial plate', icon: 'barcode-outline' },
  { purpose: 'before', label: 'Before', icon: 'camera-outline' },
  { purpose: 'after', label: 'After', icon: 'checkmark-circle-outline' },
];


export default function TaskDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { me } = useSession();
  const canPlan = me?.permissions?.includes('view_tasks') ?? false;
  const theme = useTheme();
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState('');
  const [isOffline, setIsOffline] = useState(false);
  const [courier, setCourier] = useState('');
  const [tracking, setTracking] = useState('');

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
    if (!(await confirmStep(action))) {
      return;
    }
    setIsBusy(true);
    setError('');
    try {
      await sendTaskAction(Number(id), action);
      await load();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'The action could not be completed. Please try again.');
    } finally {
      setIsBusy(false);
    }
  };

  const handleShipped = async () => {
    if (!courier) {
      setError('Please choose the shipping company.');
      return;
    }
    if (!(await confirmAction('Mark as shipped?', 'The customer will be emailed that the part is on its way.'))) {
      return;
    }
    setIsBusy(true);
    setError('');
    try {
      await markShipped(Number(id), courier, tracking.trim());
      await load();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Could not mark it shipped. Please try again.');
    } finally {
      setIsBusy(false);
    }
  };

  const handleAddPhoto = async (purpose: 'fault' | 'serial_plate' | 'before' | 'after') => {
    const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) {
      Alert.alert('Permission required', 'Access to the photo library is required to attach a photo.');
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
      // The phone's own reason too (no connection, a file it couldn't read…), so it can be fixed.
      console.warn('Photo upload failed (file-system upload)', err);
      setError(err instanceof ApiRequestError ? err.message : `Upload failed. Please try again. (${String(err)})`);
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
          {/* A supervisor (or above) plans from the task's full screen: time, team, edit, actions. */}
          {canPlan ? (
            <TouchableOpacity
              style={[styles.planButton, { borderColor: theme.primary }]}
              onPress={() => router.push(`/team-task/${task.id}`)}
            >
              <Ionicons name="options-outline" size={16} color={theme.primary} />
              <ThemedText type="small" themeColor="primary">Plan this task: time, team, edit, actions</ThemedText>
            </TouchableOpacity>
          ) : null}
          <ThemedView style={styles.whenRow}>
            <Ionicons name="time-outline" size={18} color={theme.text} />
            <ThemedText type="smallBold">{when(task)}</ThemedText>
          </ThemedView>
          {/* Get there, and call the site. */}
          <ThemedView style={styles.quickRow}>
            {task.directions_url ? (
              <TouchableOpacity
                style={[styles.quickButton, { borderColor: theme.primary }]}
                onPress={() => Linking.openURL(task.directions_url!)}
              >
                <Ionicons name="navigate-outline" size={18} color={theme.primary} />
                <ThemedText themeColor="primary">Directions</ThemedText>
              </TouchableOpacity>
            ) : null}
            {task.site_contact_phone ? (
              <TouchableOpacity
                style={[styles.quickButton, { borderColor: theme.primary }]}
                onPress={() => Linking.openURL(`tel:${task.site_contact_phone}`)}
              >
                <Ionicons name="call-outline" size={18} color={theme.primary} />
                <ThemedText themeColor="primary" numberOfLines={1}>
                  Call {task.site_contact_name || task.site_contact_phone}
                </ThemedText>
              </TouchableOpacity>
            ) : null}
          </ThemedView>
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
            {task.required_skill_name ? <Row label="Required skill" value={task.required_skill_name} /> : null}
          </ThemedView>

          {task.from_request ? (
            <CustomerRequest request={task.from_request} directionsUrl={task.directions_url} />
          ) : task.machines && task.machines.length > 0 ? (
            <ThemedView style={styles.section}>
              <ThemedText type="smallBold">Machines the customer reported</ThemedText>
              {task.machines.map((machine, index) => (
                <ThemedView key={index} style={[styles.machine, { borderColor: theme.backgroundSelected }]}>
                  <ThemedText type="smallBold">{machine.name}{machine.serial ? ` · ${machine.serial}` : ''}</ThemedText>
                  <ThemedText type="small">{machine.problem}</ThemedText>
                  {machine.description ? <ThemedText type="small" themeColor="textSecondary">{machine.description}</ThemedText> : null}
                </ThemedView>
              ))}
            </ThemedView>
          ) : task.description ? (
            <ThemedView style={styles.section}>
              <ThemedText type="smallBold">Description</ThemedText>
              <ThemedText>{task.description}</ThemedText>
            </ThemedView>
          ) : null}

          {task.shipment ? (
            // A part shipment: the parcel, and handing it to the courier (tasks.shipments).
            <ThemedView style={styles.section}>
              <ThemedText type="smallBold">Part shipment · {task.shipment.label}</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">Send to</ThemedText>
              <ThemedText>{task.shipment.send_to.customer} — {task.shipment.send_to.branch}</ThemedText>
              {task.shipment.send_to.address ? (
                <TouchableOpacity onPress={() => Linking.openURL(task.shipment!.send_to.directions_url)}>
                  <ThemedText themeColor="primary">{task.shipment.send_to.address}</ThemedText>
                </TouchableOpacity>
              ) : null}
              {task.shipment.send_to.contact_name ? <ThemedText>{task.shipment.send_to.contact_name}</ThemedText> : null}
              {task.shipment.send_to.contact_phone ? (
                <TouchableOpacity onPress={() => Linking.openURL(`tel:${task.shipment!.send_to.contact_phone}`)}>
                  <ThemedText themeColor="primary">{task.shipment.send_to.contact_phone}</ThemedText>
                </TouchableOpacity>
              ) : null}

              {task.shipment.parts.map((part, index) => (
                <ThemedText key={index} type="small">{part.code} × {part.quantity}</ThemedText>
              ))}
              {task.shipment.pak_reference_number ? <Row label="PAK" value={task.shipment.pak_reference_number} /> : null}
              {task.shipment.stage === 'shipped' ? (
                <>
                  <Row label="Courier" value={task.shipment.shipping_company || '—'} />
                  <Row label="Tracking" value={task.shipment.shipping_tracking_number || '—'} />
                </>
              ) : null}
              {task.shipment.can_ship ? (
                <ThemedView style={styles.shipForm}>
                  <ThemedText type="small" themeColor="textSecondary">Shipping company</ThemedText>
                  <ThemedView style={styles.courierRow}>
                    {task.shipment.companies.map((company) => (
                      <TouchableOpacity
                        key={company.value}
                        style={[
                          styles.courierChip, { borderColor: theme.backgroundSelected },
                          courier === company.value && { backgroundColor: theme.primary, borderColor: theme.primary },
                        ]}
                        onPress={() => setCourier(company.value)}
                      >
                        <ThemedText type="small" style={courier === company.value ? { color: theme.onPrimary } : undefined}>
                          {company.label}
                        </ThemedText>
                      </TouchableOpacity>
                    ))}
                  </ThemedView>
                  <ThemedText type="small" themeColor="textSecondary">Tracking number (leave empty if the courier has none)</ThemedText>
                  <TextInput
                    style={[styles.input, { color: theme.text, borderColor: theme.backgroundSelected }]}
                    value={tracking}
                    onChangeText={setTracking}
                    autoCapitalize="characters"
                    autoCorrect={false}
                  />
                  <TouchableOpacity
                    style={[styles.button, { backgroundColor: theme.primary }, isBusy && styles.buttonDisabled]}
                    onPress={handleShipped}
                    disabled={isBusy}
                  >
                    {isBusy ? <ActivityIndicator color={theme.onPrimary} /> : (
                      <ThemedText style={[styles.buttonText, { color: theme.onPrimary }]}>Shipped</ThemedText>
                    )}
                  </TouchableOpacity>
                  <ThemedText type="small" themeColor="textSecondary">The customer is emailed that it is on its way.</ThemedText>
                </ThemedView>
              ) : null}
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
              {isBusy ? <ActivityIndicator color={theme.onPrimary} /> : (
                <ThemedText style={[styles.buttonText, { color: theme.onPrimary }]}>{ACTION_LABELS[task.next_action]}</ThemedText>
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
              <ThemedText themeColor="textSecondary" type="small">Recorded in error? You may undo within 10 minutes.</ThemedText>
            </TouchableOpacity>
          ) : null}

          <ThemedView style={styles.section}>
            <ThemedText type="smallBold">Add a photo</ThemedText>
            <ThemedView style={styles.photoRow}>
              {PHOTO_KINDS.map(({ purpose, label, icon }) => (
                <TouchableOpacity
                  key={purpose}
                  style={[styles.photoButton, { borderColor: theme.backgroundSelected }]}
                  onPress={() => handleAddPhoto(purpose)}
                  disabled={isBusy}
                >
                  <Ionicons name={icon} size={22} color={theme.text} />
                  <ThemedText type="small">{label}</ThemedText>
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
                  {attachment.asset_name ? ` · ${attachment.asset_name}` : ''}
                </ThemedText>
              ))}
            </ThemedView>
          ) : null}

          <TaskMessages taskId={task.id} />

          {/* The report: last, just before the history. */}
          {task.can_file_report ? (
            <TouchableOpacity
              style={[styles.button, { backgroundColor: theme.primary }]}
              onPress={() => router.push(`/report/${task.id}`)}
            >
              <ThemedText style={[styles.buttonText, { color: theme.onPrimary }]}>
                {task.status === 'in_progress' ? 'Submit work report' : 'Work report'}
              </ThemedText>
            </TouchableOpacity>
          ) : null}

          {task.events.length > 0 ? (
            <ThemedView style={styles.section}>
              <ThemedText type="smallBold">History</ThemedText>
              {task.events.map((event) => (
                <ThemedText key={event.id} themeColor="textSecondary" type="small">
                  {new Date(event.occurred_at).toLocaleString([], {
                    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
                  })} · {event.event_type_display} — {event.actor_name}
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
  planButton: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.two, alignSelf: 'flex-start',
    borderWidth: 1, borderRadius: Spacing.two, paddingVertical: Spacing.two, paddingHorizontal: Spacing.three,
  },
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
  photoButton: {
    flexBasis: '47%', flexGrow: 1, alignItems: 'center', gap: Spacing.one,
    borderWidth: 1, borderRadius: Spacing.two, paddingHorizontal: Spacing.two, paddingVertical: Spacing.three,
  },
  whenRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two, backgroundColor: 'transparent' },
  quickRow: { flexDirection: 'row', gap: Spacing.two, backgroundColor: 'transparent' },
  quickButton: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: Spacing.one,
    borderWidth: 1, borderRadius: Spacing.two, paddingVertical: Spacing.two, paddingHorizontal: Spacing.two,
  },
  machine: { borderBottomWidth: 1, paddingBottom: Spacing.two, gap: 2, backgroundColor: 'transparent' },
  shipForm: { gap: Spacing.two, marginTop: Spacing.two, backgroundColor: 'transparent' },
  conversationTitle: { marginTop: Spacing.two },
  courierRow: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  courierChip: { borderWidth: 1, borderRadius: Spacing.four, paddingVertical: Spacing.one, paddingHorizontal: Spacing.three },
  input: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three, fontSize: 16 },
});
