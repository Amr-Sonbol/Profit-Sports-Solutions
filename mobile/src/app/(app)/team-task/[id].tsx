import DateTimePicker, { type DateTimePickerEvent } from '@react-native-community/datetimepicker';
import Ionicons from '@expo/vector-icons/Ionicons';
import { useCallback, useState } from 'react';
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { ActivityIndicator, Alert, Linking, Platform, ScrollView, StyleSheet, TextInput, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import {
  approveTaskReport, assignTask, createFollowUp, createNextStep, fetchAssignmentCandidates, fetchTeamTaskDetail, manageTask, type AssignAction,
} from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { hm } from '@/hours';
import type { Assignment, Candidate, TeamTaskDetail } from '@/types';
import { when } from '@/task-time';
import { ChoicePicker } from '@/components/choice-picker';

// Mirrors TaskAssignment.EndReason (tasks/models.py).
const END_REASONS: [string, string][] = [
  ['sick', 'Sick'], ['leave', 'Leave'], ['overloaded', 'Overloaded'], ['skill_mismatch', 'Skill mismatch'],
  ['customer_request', 'Customer request'], ['emergency', 'Emergency'], ['vehicle', 'Vehicle'],
  ['left_company', 'Left the company'], ['other', 'Other'],
];

// What the picker is choosing for: a new lead, an extra helper, or why a
// helper/lead is coming off the task.
type Picking =
  | { kind: 'lead' }
  | { kind: 'helper' }
  | { kind: 'lead_reason'; technician: number }
  | { kind: 'remove_reason'; assignment: Assignment };

type ManageAction = 'cancel' | 'close' | 'negligence' | 'correct_time';
const MANAGE_TITLES: Record<ManageAction, string> = {
  cancel: 'Cancel this task — why?',
  close: 'Close this task — note',
  negligence: 'Flag negligence — what happened?',
  correct_time: 'Correct the time of this tap',
};
const MANAGE_DONE: Record<ManageAction, string> = {
  cancel: 'Task cancelled.', close: 'Task closed.', negligence: 'Task flagged.', correct_time: 'Time corrected.',
};

/**
 * Receiving / loading: carry the machines that are done to the next step —
 * loading at a warehouse, or an installation at a customer's site, in any
 * country for a manager — choosing which go (a load can be split).
 */
function NextStepPanel({ taskId, nextStep, onCreated }: {
  taskId: number;
  nextStep: NonNullable<NonNullable<TeamTaskDetail['equipment']>['next_step']>;
  onCreated: (id: number) => void;
}) {
  const theme = useTheme();
  const [step, setStep] = useState(nextStep.steps[0]?.value ?? '');
  const [site, setSite] = useState('');
  const [chosen, setChosen] = useState<number[]>(nextStep.lines.map((line) => line.id));
  const [error, setError] = useState('');
  const [isBusy, setIsBusy] = useState(false);
  const sites = nextStep.steps.find((option) => option.value === step)?.sites ?? [];

  const create = async () => {
    if (!site) {
      setError('Choose where the next step is.');
      return;
    }
    setIsBusy(true);
    setError('');
    try {
      const created = await createNextStep(taskId, step, site, chosen);
      Alert.alert('Next step created', `${created.task_number}, with ${chosen.length} machines.`);
      onCreated(created.id);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Not created. Please try again.');
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <ThemedView style={[styles.manageBox, { borderColor: theme.primary }]}>
      <ThemedText type="smallBold">Create the next step</ThemedText>
      <ThemedView style={styles.quickRow}>
        {nextStep.steps.map((option) => {
          const on = step === option.value;
          return (
            <TouchableOpacity
              key={option.value}
              style={[styles.quickButton, { borderColor: theme.primary }, on && { backgroundColor: theme.primary }]}
              onPress={() => {
                setStep(option.value);
                setSite('');
              }}
            >
              <ThemedText type="small" style={{ color: on ? theme.onPrimary : theme.primary }}>{option.label}</ThemedText>
            </TouchableOpacity>
          );
        })}
      </ThemedView>
      <ChoicePicker label="Where" choices={sites} value={site} onChange={setSite} placeholder="Country — customer — site" />
      <ThemedText type="small" themeColor="textSecondary">The machines to carry, with their serials and colours:</ThemedText>
      {nextStep.lines.map((line) => {
        const on = chosen.includes(line.id);
        return (
          <TouchableOpacity
            key={line.id} style={styles.quickRow}
            onPress={() => setChosen(on ? chosen.filter((id) => id !== line.id) : [...chosen, line.id])}
          >
            <Ionicons name={on ? 'checkbox' : 'square-outline'} size={20} color={theme.primary} />
            <ThemedText type="small">{line.label}</ThemedText>
          </TouchableOpacity>
        );
      })}
      {error ? <ThemedText type="small" themeColor="danger">{error}</ThemedText> : null}
      <SecondaryButton label={`Create the next step (${chosen.length})`} onPress={create} disabled={isBusy || !chosen.length} />
    </ThemedView>
  );
}

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
      setError(err instanceof ApiRequestError ? errorText(err) : 'The request could not be completed. Please try again.');
    } finally {
      setIsBusy(false);
    }
  };

  // A manager's action being filled in: its note, and for a tap the right time.
  const [managing, setManaging] = useState<{ action: ManageAction; eventId?: number } | null>(null);
  const [manageNote, setManageNote] = useState('');
  const [manageTime, setManageTime] = useState<Date | null>(null);
  const [pickingTime, setPickingTime] = useState<'date' | 'time' | 'datetime' | null>(null);

  const startManaging = (action: ManageAction, eventId?: number, at?: string) => {
    setManaging({ action, eventId });
    setManageNote('');
    setManageTime(at ? new Date(at) : null);
  };

  const saveManaging = () => {
    if (!managing || !task) {
      return;
    }
    const pad = (n: number) => String(n).padStart(2, '0');
    const local = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    const body: Record<string, string | number> = managing.action === 'correct_time'
      ? { action: 'correct_time', event_id: managing.eventId ?? 0, corrected_at: manageTime ? local(manageTime) : '', reason: manageNote }
      : { action: managing.action, note: manageNote };
    run(() => manageTask(task.id, body), MANAGE_DONE[managing.action]).then(() => setManaging(null));
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
          <ThemedView style={styles.quickRow}>
            <Ionicons name="time-outline" size={18} color={theme.text} />
            <ThemedText type="smallBold">{when(task)}</ThemedText>
          </ThemedView>
          <ThemedView style={styles.quickRow}>
            {task.directions_url ? (
              <TouchableOpacity
                style={[styles.quickButton, { borderColor: theme.primary }]}
                onPress={() => Linking.openURL(task.directions_url!)}
              >
                <Ionicons name="navigate-outline" size={16} color={theme.primary} />
                <ThemedText type="small" themeColor="primary">Directions</ThemedText>
              </TouchableOpacity>
            ) : null}
            {task.site_contact_phone ? (
              <TouchableOpacity
                style={[styles.quickButton, { borderColor: theme.primary }]}
                onPress={() => Linking.openURL(`tel:${task.site_contact_phone}`)}
              >
                <Ionicons name="call-outline" size={16} color={theme.primary} />
                <ThemedText type="small" themeColor="primary" numberOfLines={1}>
                  Call {task.site_contact_name || task.site_contact_phone}
                </ThemedText>
              </TouchableOpacity>
            ) : null}
            {/* Everything else on the website's task page: edit, schedule and delay
                notices to the customer, messages, documents, feedback request... */}
            <TouchableOpacity
              style={[styles.quickButton, { borderColor: theme.primary }]}
              onPress={() => router.push({
                pathname: '/website', params: { path: `/tasks/${task.id}/`, title: task.task_number },
              })}
            >
              <Ionicons name="open-outline" size={16} color={theme.primary} />
              <ThemedText type="small" themeColor="primary">Full task page</ThemedText>
            </TouchableOpacity>
          </ThemedView>

          <ThemedView style={[styles.section, { backgroundColor: theme.backgroundElement }]}>
            <Row label="Status" value={task.status_display} />
            <Row label="Priority" value={task.priority_display} />
            {task.task_type_name ? <Row label="Type" value={task.task_type_name} /> : null}
            {task.brand_name ? <Row label="Brand" value={task.brand_name} /> : null}
            {task.required_skill_name ? <Row label="Required skill" value={task.required_skill_name} /> : null}
            {task.responsible_supervisor_name ? (
              <Row label="Supervisor" value={task.responsible_supervisor_name} />
            ) : null}
            {task.estimated_hours ? <Row label="Estimated hours" value={hm(task.estimated_hours)} /> : null}
          </ThemedView>

          {task.description ? (
            <ThemedView style={styles.section}>
              <ThemedText type="smallBold">Description</ThemedText>
              <ThemedText>{task.description}</ThemedText>
            </ThemedView>
          ) : null}

          {task.machines && task.machines.length > 0 ? (
            <ThemedView style={styles.section}>
              <ThemedText type="smallBold">Machines the customer reported</ThemedText>
              {task.machines.map((machine, index) => (
                <ThemedView key={index} style={[styles.machine, { borderColor: theme.backgroundSelected }]}>
                  <ThemedText type="smallBold">{machine.name}{machine.serial ? ` · ${machine.serial}` : ''}</ThemedText>
                  <ThemedText type="small">
                    {machine.problem}{machine.out_of_service ? ' · cannot be used' : ''}
                  </ThemedText>
                  {machine.description ? (
                    <ThemedText type="small" themeColor="textSecondary">{machine.description}</ThemedText>
                  ) : null}
                </ThemedView>
              ))}
            </ThemedView>
          ) : null}

          {task.attachments.length > 0 ? (
            <ThemedView style={styles.section}>
              <ThemedText type="smallBold">Photos and files ({task.attachments.length})</ThemedText>
              {task.attachments.map((attachment) => (
                <TouchableOpacity key={attachment.id} onPress={() => Linking.openURL(attachment.url)}>
                  <ThemedText type="small" themeColor="primary">
                    {attachment.purpose_display} — {attachment.media_type_display} ›
                  </ThemedText>
                </TouchableOpacity>
              ))}
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
                {picking.kind === 'lead' ? 'Select the lead technician' : 'Select a helper'}
              </ThemedText>
              {candidates.length === 0 ? (
                <ThemedText themeColor="textSecondary">There are no other technicians in this country.</ThemedText>
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
                    {candidate.visiting_from ? ` (visiting from ${candidate.visiting_from})` : ''}
                  </ThemedText>
                  <ThemedText themeColor="textSecondary" type="small">
                    {!candidate.is_available
                      ? 'Unavailable'
                      : candidate.next_task_number
                        ? `Next: ${candidate.next_task_number}${candidate.next_task_at
                          ? ` on ${new Date(candidate.next_task_at).toLocaleString()}` : ''}`
                        : 'No bookings'}
                  </ThemedText>
                </TouchableOpacity>
              ))}
              <SecondaryButton label="Cancel" onPress={() => setPicking(null)} />
            </ThemedView>
          ) : null}

          {picking?.kind === 'lead_reason' || picking?.kind === 'remove_reason' ? (
            <ThemedView style={[styles.section, styles.picker, { borderColor: theme.backgroundSelected }]}>
              <ThemedText type="smallBold">
                {picking.kind === 'lead_reason' ? 'Reason for replacing the current lead' : 'Reason for removing this helper'}
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
              <TouchableOpacity
                onPress={() => router.push({
                  pathname: '/website', params: { path: `/tasks/${task.id}/report.pdf`, title: `${task.task_number} report` },
                })}
              >
                <ThemedText type="small" themeColor="primary">View the customer&apos;s report ›</ThemedText>
              </TouchableOpacity>
              <Row label="Resolved" value={task.report.resolved ? 'Yes' : 'No'} />
              <Row label="Time on site" value={hm(task.report.labour_hours)} />
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
                      {part.quantity} × {part.part_code}{part.description ? ` (${part.description})` : ''}
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
                task.can_manager_approve ? 'Report approved. Task closed.' : 'Approved. Awaiting manager approval.',
              )}
              disabled={isBusy}
            >
              {isBusy ? <ActivityIndicator color={theme.onPrimary} /> : (
                <ThemedText style={[styles.buttonText, { color: theme.onPrimary }]}>
                  {task.can_manager_approve ? 'Approve and close' : 'Approve report'}
                </ThemedText>
              )}
            </TouchableOpacity>
          ) : null}

          {task.equipment ? (
            <ThemedView style={styles.section}>
              <ThemedText type="smallBold">Machines</ThemedText>
              {task.equipment.previous_task ? (
                <TouchableOpacity onPress={() => router.push(`/team-task/${task.equipment!.previous_task!.id}`)}>
                  <ThemedText type="small" themeColor="primary">Previous step: {task.equipment.previous_task.task_number} ›</ThemedText>
                </TouchableOpacity>
              ) : null}
              {task.equipment.next_tasks.map((next) => (
                <TouchableOpacity key={next.id} onPress={() => router.push(`/team-task/${next.id}`)}>
                  <ThemedText type="small" themeColor="primary">Next step: {next.task_number} ›</ThemedText>
                </TouchableOpacity>
              ))}
              {task.equipment.lines.map((line, index) => (
                <ThemedView key={index} style={[styles.machine, { borderColor: theme.backgroundSelected }]}>
                  <ThemedText type="smallBold">
                    {line.product_code}{line.serial_number ? ` · ${line.serial_number}` : ''}{line.added_on_site ? ' · added on site' : ''}
                  </ThemedText>
                  <ThemedText type="small" themeColor={line.status === 'done' ? 'text' : line.status === 'pending' ? 'textSecondary' : 'danger'}>
                    {line.status_label} · {line.colors}
                  </ThemedText>
                  {line.replacement ? <ThemedText type="small">Instead: {line.replacement}</ThemedText> : null}
                  {line.note ? <ThemedText type="small" themeColor="textSecondary">{line.note}</ThemedText> : null}
                  {line.photo_url ? (
                    <TouchableOpacity onPress={() => Linking.openURL(line.photo_url)}>
                      <ThemedText type="small" themeColor="primary">Photo ›</ThemedText>
                    </TouchableOpacity>
                  ) : null}
                </ThemedView>
              ))}
              {task.equipment.next_step ? (
                <NextStepPanel
                  taskId={task.id} nextStep={task.equipment.next_step}
                  onCreated={(id) => router.push(`/team-task/${id}`)}
                />
              ) : null}
              {task.equipment.still_to_install ? (
                <ThemedView style={[styles.manageBox, { borderColor: theme.primary }]}>
                  <ThemedText type="small">
                    {task.equipment.still_to_install} {task.equipment.still_to_install === 1 ? 'machine is' : 'machines are'} still to install.
                  </ThemedText>
                  {task.equipment.can_follow_up ? (
                    <SecondaryButton
                      label="Create a follow-up task for them" disabled={isBusy}
                      onPress={async () => {
                        setIsBusy(true);
                        try {
                          const created = await createFollowUp(task.id);
                          router.push(`/team-task/${created.id}`);
                        } catch (err) {
                          setError(err instanceof ApiRequestError ? errorText(err) : 'Not created. Please try again.');
                        } finally {
                          setIsBusy(false);
                        }
                      }}
                    />
                  ) : null}
                </ThemedView>
              ) : null}
            </ThemedView>
          ) : null}

          {task.manage && (task.manage.can_cancel || task.manage.can_close || task.manage.can_flag) ? (
            <ThemedView style={styles.section}>
              <ThemedText type="smallBold">Manager</ThemedText>
              <ThemedView style={styles.quickRow}>
                {task.manage.can_cancel ? (
                  <SecondaryButton label="Cancel task" onPress={() => startManaging('cancel')} disabled={isBusy} />
                ) : null}
                {task.manage.can_close ? (
                  <SecondaryButton label="Close task" onPress={() => startManaging('close')} disabled={isBusy} />
                ) : null}
                {task.manage.can_flag ? (
                  <SecondaryButton label="Flag negligence" onPress={() => startManaging('negligence')} disabled={isBusy} />
                ) : null}
              </ThemedView>
            </ThemedView>
          ) : null}

          {managing ? (
            <ThemedView style={[styles.section, styles.manageBox, { borderColor: theme.primary }]}>
              <ThemedText type="smallBold">{MANAGE_TITLES[managing.action]}</ThemedText>
              {managing.action === 'correct_time' ? (
                <TouchableOpacity
                  style={[styles.quickButton, { borderColor: theme.primary }]}
                  onPress={() => setPickingTime(Platform.OS === 'ios' ? 'datetime' : 'date')}
                >
                  <ThemedText type="small" themeColor="primary">
                    {manageTime ? manageTime.toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : 'Pick the right time'}
                  </ThemedText>
                </TouchableOpacity>
              ) : null}
              <TextInput
                style={[styles.manageInput, { color: theme.text, borderColor: theme.backgroundSelected }]}
                value={manageNote} onChangeText={setManageNote} multiline
                placeholder={managing.action === 'correct_time' ? 'Reason' : managing.action === 'negligence' ? 'What happened' : 'Note'}
                placeholderTextColor={theme.textSecondary}
              />
              <ThemedView style={styles.quickRow}>
                <SecondaryButton label="Save" onPress={saveManaging} disabled={isBusy} />
                <SecondaryButton label="Back" onPress={() => setManaging(null)} />
              </ThemedView>
            </ThemedView>
          ) : null}
          {pickingTime && manageTime ? (
            <DateTimePicker
              value={manageTime} mode={pickingTime} display={Platform.OS === 'ios' ? 'inline' : 'default'}
              onChange={(event: DateTimePickerEvent, date?: Date) => {
                if (event.type === 'dismissed' || !date) {
                  setPickingTime(null);
                  return;
                }
                const next = new Date(manageTime);
                if (pickingTime === 'time') {
                  next.setHours(date.getHours(), date.getMinutes());
                } else if (pickingTime === 'date') {
                  next.setFullYear(date.getFullYear(), date.getMonth(), date.getDate());
                } else {
                  next.setTime(date.getTime());
                }
                setManageTime(next);
                setPickingTime(pickingTime === 'date' ? 'time' : null);
              }}
            />
          ) : null}

          {task.events.length > 0 ? (
            <ThemedView style={styles.section}>
              <ThemedText type="smallBold">History</ThemedText>
              {task.events.map((event) => (
                <ThemedView key={event.id} style={styles.eventRow}>
                  <ThemedText themeColor="textSecondary" type="small" style={styles.eventText}>
                    {new Date(event.corrected_at ?? event.occurred_at).toLocaleString([], {
                      day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
                    })}{event.corrected_at ? ' (corrected)' : ''} · {event.event_type_display} — {event.actor_name}
                  </ThemedText>
                  {task.manage?.correctable_event_ids.includes(event.id) ? (
                    <TouchableOpacity onPress={() => startManaging('correct_time', event.id, event.corrected_at ?? event.occurred_at)}>
                      <ThemedText type="small" themeColor="primary">Correct time</ThemedText>
                    </TouchableOpacity>
                  ) : null}
                </ThemedView>
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
  manageBox: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three },
  manageInput: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, minHeight: 60, textAlignVertical: 'top' },
  eventRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two, backgroundColor: 'transparent' },
  eventText: { flex: 1 },
  quickRow: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: Spacing.two, backgroundColor: 'transparent' },
  quickButton: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.one, borderWidth: 1, borderRadius: Spacing.two,
    paddingHorizontal: Spacing.three, paddingVertical: Spacing.one,
  },
  machine: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, gap: 2 },
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
