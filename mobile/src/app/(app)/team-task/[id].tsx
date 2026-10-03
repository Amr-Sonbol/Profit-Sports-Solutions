import { useCallback, useState } from 'react';
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { ActivityIndicator, Alert, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import {
  type AssignAction, approveTaskReport, assignTask, fetchAssignmentCandidates, fetchTeamTaskDetail,
} from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { Assignment, Candidate, TeamTaskDetail } from '@/types';

// Mirrors TaskAssignment.EndReason (tasks/models.py).
const END_REASONS: [string, string][] = [
  ['sick', 'Sick'], ['leave', 'Leave'], ['overloaded', 'Overloaded'], ['skill_mismatch', 'Skill mismatch'],
  ['customer_request', 'Customer request'], ['emergency', 'Emergency'], ['vehicle', 'Vehicle'], ['other', 'Other'],
];

// What the picker is choosing for: a new lead, an extra helper, or why a
// helper/lead is coming off the task.
type Picking =
  | { kind: 'lead' }
  | { kind: 'helper' }
  | { kind: 'lead_reason'; technician: number }
  | { kind: 'remove_reason'; assignment: Assignment };

export default function TeamTaskScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const theme = useTheme();
  const [task, setTask] = useState<TeamTaskDetail | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [picking, setPicking] = useState<Picking | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      setTask(await fetchTeamTaskDetail(Number(id)));
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Could not load this task.');
    }
  }, [id]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const run = async (action: () => Promise<TeamTaskDetail>, success: string) => {
    setIsBusy(true);
    setError('');
    try {
      setTask(await action());
      setPicking(null);
      Alert.alert('Done', success);
    } catch (err) {
      setError(err instanceof ApiRequestError ? errorText(err) : 'That didn’t work — try again.');
    } finally {
      setIsBusy(false);
    }
  };

  const startPicking = async (kind: 'lead' | 'helper') => {
    setError('');
    try {
      setCandidates(await fetchAssignmentCandidates(Number(id)));
      setPicking({ kind });
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Could not load technicians.');
    }
  };

  const pickTechnician = (candidate: Candidate) => {
    if (!task || !picking) {
      return;
    }
    if (picking.kind === 'helper') {
      send({ action: 'add_helper', technician: candidate.id }, 'Helper added.');
    } else if (task.lead) {
      // Replacing someone — the server wants to know why.
      setPicking({ kind: 'lead_reason', technician: candidate.id });
    } else {
      send({ action: 'set_lead', technician: candidate.id }, 'Lead technician set.');
    }
  };

  const pickReason = (reason: string) => {
    if (picking?.kind === 'lead_reason') {
      send({ action: 'set_lead', technician: picking.technician, end_reason: reason }, 'Lead technician replaced.');
    } else if (picking?.kind === 'remove_reason') {
      send({ action: 'remove_helper', assignment_id: picking.assignment.id, end_reason: reason }, 'Helper removed.');
    }
  };

  const send = (body: AssignAction, message: string) => run(() => assignTask(Number(id), body), message);

  if (!task) {
    return (
      <ThemedView style={styles.centered}>
        {error ? <ThemedText themeColor="danger">{error}</ThemedText> : <ActivityIndicator />}
      </ThemedView>
    );
  }

  const canApprove = task.can_supervisor_approve || task.can_manager_approve;

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backLink}>
          <ThemedText themeColor="primary">← Back</ThemedText>
        </TouchableOpacity>

        <ScrollView contentContainerStyle={styles.content}>
          <ThemedText type="title" style={styles.taskNumber}>{task.task_number}</ThemedText>
          <ThemedText type="subtitle" style={styles.customer}>{task.customer_name}</ThemedText>
          <ThemedText themeColor="textSecondary">{task.site_name} — {task.site_address}</ThemedText>

          <ThemedView style={[styles.section, { backgroundColor: theme.backgroundElement }]}>
            <Row label="Status" value={task.status_display} />
            <Row label="Priority" value={task.priority_display} />
            {task.task_type_name ? <Row label="Type" value={task.task_type_name} /> : null}
            {task.responsible_supervisor_name ? (
              <Row label="Supervisor" value={task.responsible_supervisor_name} />
            ) : null}
            {task.estimated_hours ? <Row label="Estimated hours" value={task.estimated_hours} /> : null}
          </ThemedView>

          {task.description ? (
            <ThemedView style={styles.section}>
              <ThemedText type="smallBold">Description</ThemedText>
              <ThemedText>{task.description}</ThemedText>
            </ThemedView>
          ) : null}

          {error ? <ThemedText themeColor="danger" style={styles.centerText}>{error}</ThemedText> : null}

          <ThemedView style={styles.section}>
            <ThemedText type="smallBold">Team</ThemedText>
            <ThemedText>Lead: {task.lead ? task.lead.technician_name : 'not assigned yet'}</ThemedText>
            {task.helpers.map((helper) => (
              <ThemedView key={helper.id} style={styles.helperRow}>
                <ThemedText>Helper: {helper.technician_name}</ThemedText>
                {task.can_assign ? (
                  <TouchableOpacity onPress={() => setPicking({ kind: 'remove_reason', assignment: helper })}>
                    <ThemedText themeColor="danger" type="small">Remove</ThemedText>
                  </TouchableOpacity>
                ) : null}
              </ThemedView>
            ))}
            {task.can_assign ? (
              <ThemedView style={styles.buttonRow}>
                <SecondaryButton
                  label={task.lead ? 'Change lead' : 'Assign lead'} onPress={() => startPicking('lead')}
                  disabled={isBusy}
                />
                {task.lead ? (
                  <SecondaryButton label="Add helper" onPress={() => startPicking('helper')} disabled={isBusy} />
                ) : null}
              </ThemedView>
            ) : null}
          </ThemedView>

          {picking?.kind === 'lead' || picking?.kind === 'helper' ? (
            <ThemedView style={[styles.section, styles.picker, { borderColor: theme.backgroundSelected }]}>
              <ThemedText type="smallBold">
                {picking.kind === 'lead' ? 'Pick the lead technician' : 'Pick a helper'}
              </ThemedText>
              {candidates.length === 0 ? (
                <ThemedText themeColor="textSecondary">No other technicians in this country.</ThemedText>
              ) : null}
              {candidates.map((candidate) => (
                <TouchableOpacity
                  key={candidate.id}
                  style={[styles.candidate, { borderColor: theme.backgroundSelected }]}
                  onPress={() => pickTechnician(candidate)}
                  disabled={!candidate.is_available || isBusy}
                >
                  <ThemedText style={!candidate.is_available ? styles.unavailable : undefined}>
                    {candidate.full_name}
                    {candidate.skill_level != null ? ` — level ${candidate.skill_level}` : ''}
                  </ThemedText>
                  <ThemedText themeColor="textSecondary" type="small">
                    {!candidate.is_available
                      ? 'Unavailable'
                      : candidate.next_task_number
                        ? `Next: ${candidate.next_task_number}${candidate.next_task_at
                          ? ` on ${new Date(candidate.next_task_at).toLocaleString()}` : ''}`
                        : 'Nothing booked'}
                  </ThemedText>
                </TouchableOpacity>
              ))}
              <SecondaryButton label="Cancel" onPress={() => setPicking(null)} />
            </ThemedView>
          ) : null}

          {picking?.kind === 'lead_reason' || picking?.kind === 'remove_reason' ? (
            <ThemedView style={[styles.section, styles.picker, { borderColor: theme.backgroundSelected }]}>
              <ThemedText type="smallBold">
                {picking.kind === 'lead_reason' ? 'Why is the current lead being replaced?' : 'Why is this helper coming off?'}
              </ThemedText>
              <ThemedView style={styles.reasonGrid}>
                {END_REASONS.map(([value, label]) => (
                  <TouchableOpacity
                    key={value}
                    style={[styles.reason, { borderColor: theme.backgroundSelected }]}
                    onPress={() => pickReason(value)}
                    disabled={isBusy}
                  >
                    <ThemedText type="small">{label}</ThemedText>
                  </TouchableOpacity>
                ))}
              </ThemedView>
              <SecondaryButton label="Cancel" onPress={() => setPicking(null)} />
            </ThemedView>
          ) : null}

          {task.report ? (
            <ThemedView style={[styles.section, { backgroundColor: theme.backgroundElement }]}>
              <ThemedText type="smallBold">Work report</ThemedText>
              <Row label="Resolved" value={task.report.resolved ? 'Yes' : 'No'} />
              <Row label="Labour hours" value={task.report.labour_hours} />
              <Row label="Signed by" value={task.report.customer_name || '—'} />
              <ThemedText type="small" themeColor="textSecondary">Findings</ThemedText>
              <ThemedText>{task.report.findings}</ThemedText>
              {task.report.action_taken ? (
                <>
                  <ThemedText type="small" themeColor="textSecondary">Action taken</ThemedText>
                  <ThemedText>{task.report.action_taken}</ThemedText>
                </>
              ) : null}
              {task.report.parts_used.length > 0 ? (
                <>
                  <ThemedText type="small" themeColor="textSecondary">Parts used</ThemedText>
                  {task.report.parts_used.map((part, index) => (
                    <ThemedText key={index} type="small">
                      {part.quantity} × {part.part_code}{part.description ? ` (${part.description})` : ''} —{' '}
                      {part.unit_cost} {part.currency_code}
                    </ThemedText>
                  ))}
                </>
              ) : null}
            </ThemedView>
          ) : null}

          {canApprove ? (
            <TouchableOpacity
              style={[styles.button, { backgroundColor: theme.primary }, isBusy && styles.buttonDisabled]}
              onPress={() => run(
                () => approveTaskReport(Number(id)),
                task.can_manager_approve ? 'Report approved. Task closed.' : 'Approved — now awaiting manager approval.',
              )}
              disabled={isBusy}
            >
              {isBusy ? <ActivityIndicator color="#fff" /> : (
                <ThemedText style={styles.buttonText}>
                  {task.can_manager_approve ? 'Approve and close' : 'Approve report'}
                </ThemedText>
              )}
            </TouchableOpacity>
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

/** The first message out of a DRF error body — a detail, or the first field error. */
function errorText(err: ApiRequestError): string {
  const body = err.body as Record<string, unknown> | null;
  if (body && !('detail' in body)) {
    const first = Object.values(body)[0];
    if (Array.isArray(first) && first.length) {
      return String(first[0]);
    }
  }
  return err.message;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <ThemedView style={styles.detailRow}>
      <ThemedText themeColor="textSecondary" type="small">{label}</ThemedText>
      <ThemedText type="small">{value}</ThemedText>
    </ThemedView>
  );
}

function SecondaryButton({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) {
  const theme = useTheme();
  return (
    <TouchableOpacity
      style={[styles.secondaryButton, { borderColor: theme.primary }, disabled && styles.buttonDisabled]}
      onPress={onPress}
      disabled={disabled}
    >
      <ThemedText themeColor="primary">{label}</ThemedText>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  centerText: { textAlign: 'center' },
  backLink: { paddingHorizontal: Spacing.three, paddingVertical: Spacing.two },
  content: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.six, gap: Spacing.three },
  taskNumber: { fontSize: 24, marginBottom: 0 },
  customer: { fontSize: 18, marginBottom: 0 },
  section: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  picker: { borderWidth: 1, gap: Spacing.two },
  detailRow: { flexDirection: 'row', justifyContent: 'space-between', backgroundColor: 'transparent' },
  helperRow: { flexDirection: 'row', justifyContent: 'space-between', backgroundColor: 'transparent' },
  buttonRow: { flexDirection: 'row', gap: Spacing.two, marginTop: Spacing.two, backgroundColor: 'transparent' },
  candidate: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two },
  unavailable: { opacity: 0.5 },
  reasonGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  reason: { borderWidth: 1, borderRadius: Spacing.two, paddingHorizontal: Spacing.two, paddingVertical: Spacing.two },
  secondaryButton: {
    borderWidth: 1, borderRadius: Spacing.two, paddingVertical: Spacing.two, paddingHorizontal: Spacing.three,
    alignItems: 'center',
  },
  button: { borderRadius: Spacing.two, paddingVertical: Spacing.three, alignItems: 'center' },
  buttonDisabled: { opacity: 0.6 },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '600' },
});
