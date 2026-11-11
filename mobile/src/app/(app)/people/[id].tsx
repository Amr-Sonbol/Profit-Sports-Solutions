import Ionicons from '@expo/vector-icons/Ionicons';
import { Image } from 'expo-image';
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { ActivityIndicator, Linking, RefreshControl, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { TextInput } from '@/components/text-field';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { deactivatePerson, fetchPerson, reviewPersonSkill, setPersonAvailability } from '@/api/endpoints';
import { OfflineNotice } from '@/components/offline-notice';
import { withCountryCopy } from '@/offline';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { MySkills, Person, SkillRow } from '@/types';
import { ChoicePicker } from '@/components/choice-picker';
import { PersonWork } from '@/components/person-work';
import { alert, t } from '@/i18n';

/**
 * Someone on the viewer's team (Team → a name): photo and contacts,
 * availability (set by whoever assigns tasks), their work record, their
 * schedule, and every skill and conduct area — which a supervisor can
 * confirm or adjust here, as on the website's review screen. The API
 * decides who may be opened and what may be changed.
 */
export default function PersonScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const theme = useTheme();
  const [person, setPerson] = useState<Person | null>(null);
  const [error, setError] = useState('');
  // Showing the last copy kept on the phone (no connection).
  const [offline, setOffline] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [open, setOpen] = useState('');
  const [choosingReason, setChoosingReason] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  // Deactivating someone who left: why, and who takes over their open tasks.
  const [leaving, setLeaving] = useState(false);
  const [reason, setReason] = useState('');
  const [moveTo, setMoveTo] = useState('');

  const load = useCallback(async () => {
    try {
      const copy = await withCountryCopy(`person-${id}`, () => fetchPerson(Number(id)));
      setPerson(copy.data);
      setOffline(copy.offline);
      setError('');
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Could not load this person.');
    }
  }, [id]);

  useFocusEffect(useCallback(() => {
    load();
  }, [load]));

  const changeAvailability = async (available: boolean, reason = '') => {
    setIsBusy(true);
    try {
      setPerson(await setPersonAvailability(Number(id), available, reason));
      setChoosingReason(false);
      setError('');
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Not saved. Please try again.');
    } finally {
      setIsBusy(false);
    }
  };

  if (!person) {
    return (
      <ThemedView style={styles.centered}>
        {error ? <ThemedText themeColor="danger">{error}</ThemedText> : <ActivityIndicator />}
      </ThemedView>
    );
  }

  const { skills, record } = person;
  const progress = skills.progress;
  const group = (title: string, rows: SkillRow[]) => (rows.length ? (
    <ThemedView style={styles.section}>
      <ThemedText type="subtitle">{title}</ThemedText>
      {rows.map((row) => (
        <ReviewCard
          key={`${row.kind}-${row.id}`} row={row} levels={skills.levels} personId={person.id}
          canReview={person.can_review_skills} isOpen={open === `${row.kind}-${row.id}`}
          onToggle={() => setOpen(open === `${row.kind}-${row.id}` ? '' : `${row.kind}-${row.id}`)}
          onSaved={(updated) => {
            setPerson(updated);
            setOpen('');
          }}
        />
      ))}
    </ThemedView>
  ) : null);

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <ScrollView
          contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled"
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => {
            setRefreshing(true);
            await load();
            setRefreshing(false);
          }} />}
        >
          <TouchableOpacity onPress={() => router.back()}>
            <ThemedText themeColor="primary">← Back</ThemedText>
          </TouchableOpacity>
          <OfflineNotice offline={offline} />

          <ThemedView style={styles.header}>
            {person.photo_url ? (
              <Image source={{ uri: person.photo_url }} style={styles.photo} contentFit="cover" />
            ) : (
              <ThemedView style={[styles.photo, styles.noPhoto, { backgroundColor: theme.primary }]}>
                <ThemedText style={[styles.initials, { color: theme.onPrimary }]}>
                  {person.name.split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase()}
                </ThemedText>
              </ThemedView>
            )}
            <ThemedText type="subtitle">{person.name}</ThemedText>
            <ThemedText type="small" themeColor="textSecondary">{person.role} · {person.country}</ThemedText>
            {progress.is_certified ? <ThemedText type="smallBold" themeColor="primary">Certified</ThemedText> : null}
          </ThemedView>

          <ThemedView style={styles.row}>
            {person.phone ? (
              <TouchableOpacity
                style={[styles.action, { borderColor: theme.primary }]}
                onPress={() => Linking.openURL(`tel:${person.phone}`)}
              >
                <Ionicons name="call-outline" size={16} color={theme.primary} />
                <ThemedText type="small" themeColor="primary">Call</ThemedText>
              </TouchableOpacity>
            ) : null}
            {person.email ? (
              <TouchableOpacity
                style={[styles.action, { borderColor: theme.primary }]}
                onPress={() => Linking.openURL(`mailto:${person.email}`)}
              >
                <Ionicons name="mail-outline" size={16} color={theme.primary} />
                <ThemedText type="small" themeColor="primary">Email</ThemedText>
              </TouchableOpacity>
            ) : null}
            <TouchableOpacity
              style={[styles.action, { borderColor: theme.primary }]}
              onPress={() => router.push({ pathname: '/schedule/[id]', params: { id: String(person.id), name: person.name } })}
            >
              <Ionicons name="calendar-outline" size={16} color={theme.primary} />
              <ThemedText type="small" themeColor="primary">Schedule</ThemedText>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.action, { borderColor: theme.primary }]}
              onPress={() => router.push({
                pathname: '/website', params: { path: `/tasks/technicians/${person.id}/edit/`, title: person.name },
              })}
            >
              <Ionicons name="create-outline" size={16} color={theme.primary} />
              <ThemedText type="small" themeColor="primary">Edit details</ThemedText>
            </TouchableOpacity>
          </ThemedView>

          <PersonWork person={person} />

          <ThemedView style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
            <ThemedText type="smallBold">Availability</ThemedText>
            <ThemedText>
              {person.is_available ? 'Available for work' : t('Unavailable — {reason}', { reason: person.unavailable_reason })}
            </ThemedText>
            {person.can_set_availability ? (
              person.is_available ? (
                choosingReason ? (
                  <ThemedView style={styles.row}>
                    {person.unavailable_reasons.map((reason) => (
                      <TouchableOpacity
                        key={reason.value} disabled={isBusy}
                        style={[styles.chip, { borderColor: theme.primary }]}
                        onPress={() => changeAvailability(false, reason.value)}
                      >
                        <ThemedText type="small" themeColor="primary">{reason.label}</ThemedText>
                      </TouchableOpacity>
                    ))}
                    <TouchableOpacity style={styles.chip} onPress={() => setChoosingReason(false)}>
                      <ThemedText type="small" themeColor="textSecondary">Cancel</ThemedText>
                    </TouchableOpacity>
                  </ThemedView>
                ) : (
                  <TouchableOpacity
                    style={[styles.chip, { borderColor: theme.primary }]} onPress={() => setChoosingReason(true)}
                  >
                    <ThemedText type="small" themeColor="primary">Mark unavailable…</ThemedText>
                  </TouchableOpacity>
                )
              ) : (
                <TouchableOpacity
                  style={[styles.chip, { borderColor: theme.primary }]} disabled={isBusy}
                  onPress={() => changeAvailability(true)}
                >
                  <ThemedText type="small" themeColor="primary">Mark available</ThemedText>
                </TouchableOpacity>
              )
            ) : null}
            <ThemedText type="small" themeColor="textSecondary">
              Applies to every task: someone unavailable isn&apos;t offered for new work.
            </ThemedText>
          </ThemedView>

          <ThemedView style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
            <ThemedText type="smallBold">Work record</ThemedText>
            <ThemedText type="small">Led and completed: {record.completed} · Helped on: {record.helped}</ThemedText>
            <ThemedText type="small">
              On time: {record.on_time_percent !== null ? `${record.on_time_percent}%` : 'not enough tasks yet'}
              {record.acceptance_minutes !== null ? ` · ${t('Accepts in about {minutes} min', { minutes: record.acceptance_minutes })}` : ''}
            </ThemedText>
          </ThemedView>

          <ThemedView style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
            <ThemedText type="smallBold">Skills</ThemedText>
            <ThemedText type="small">
              Basic {progress.basic_confirmed}/{progress.basic_total} · Advanced {progress.advanced_confirmed}/
              {progress.advanced_total} · Conduct {progress.conduct_confirmed}/{progress.conduct_total}
            </ThemedText>
            <ThemedText type="small" themeColor={progress.waiting ? 'primary' : 'textSecondary'}>
              {progress.waiting} waiting for review
            </ThemedText>
          </ThemedView>

          {group('Basic level', skills.basic)}
          {group('Advanced level', skills.advanced)}
          {group('Conduct', skills.conduct)}
          {person.can_deactivate ? (
            <ThemedView style={[styles.card, { backgroundColor: theme.backgroundElement }]}>
              <ThemedText type="smallBold">Left the company?</ThemedText>
              {!leaving ? (
                <TouchableOpacity style={[styles.chip, { borderColor: theme.danger }]} onPress={() => setLeaving(true)}>
                  <ThemedText type="small" themeColor="danger">Deactivate…</ThemedText>
                </TouchableOpacity>
              ) : (
                <ThemedView style={styles.form}>
                  <ThemedText type="small" themeColor="textSecondary">
                    Their login stops working and they are signed out of every phone. Their history stays.
                  </ThemedText>
                  <TextInput
                    style={[styles.input, { color: theme.text, borderColor: theme.backgroundSelected }]}
                    value={reason} onChangeText={setReason} placeholder="Reason (e.g. resigned)"
                    placeholderTextColor={theme.textSecondary}
                  />
                  {person.open_tasks?.length ? (
                    <>
                      <ThemedText type="small" themeColor="danger">
                        Still on {person.open_tasks.length} open task{person.open_tasks.length === 1 ? '' : 's'}: {person.open_tasks.join(', ')}
                      </ThemedText>
                      <ChoicePicker
                        label="Move all their open tasks to" choices={person.move_to_choices ?? []}
                        value={moveTo} onChange={setMoveTo} placeholder="Choose a colleague"
                      />
                    </>
                  ) : null}
                  <TouchableOpacity
                    style={[styles.saveButton, { backgroundColor: theme.danger }]} disabled={isBusy}
                    onPress={() => {
                      if (!reason.trim()) {
                        setError('Give a reason.');
                        return;
                      }
                      alert(t('Deactivate {name}?', { name: person.name }), 'They will not be able to sign in.', [
                        { text: 'Cancel', style: 'cancel' },
                        {
                          text: 'Deactivate', style: 'destructive', onPress: async () => {
                            setIsBusy(true);
                            try {
                              const result = await deactivatePerson(person.id, reason, moveTo);
                              alert('Deactivated', result.still_to_reassign
                                ? t('{count} open task(s) still need someone — open each one to reassign.', { count: result.still_to_reassign })
                                : result.moved ? t('{count} open task(s) moved.', { count: result.moved }) : 'Done.');
                              router.back();
                            } catch (err) {
                              setError(err instanceof ApiRequestError ? err.message : 'Not done. Please try again.');
                            } finally {
                              setIsBusy(false);
                            }
                          },
                        },
                      ]);
                    }}
                  >
                    <ThemedText type="smallBold" style={{ color: '#fff' }}>Deactivate</ThemedText>
                  </TouchableOpacity>
                </ThemedView>
              )}
            </ThemedView>
          ) : null}
          {error ? <ThemedText themeColor="danger">{error}</ThemedText> : null}
        </ScrollView>
      </SafeAreaView>
    </ThemedView>
  );
}

/** One skill or conduct area, and — for a supervisor — confirming its level. */
function ReviewCard({ row, levels, personId, canReview, isOpen, onToggle, onSaved }: {
  row: SkillRow; levels: MySkills['levels']; personId: number; canReview: boolean;
  isOpen: boolean; onToggle: () => void; onSaved: (person: Person) => void;
}) {
  const theme = useTheme();
  const [level, setLevel] = useState<number | null>(row.level);
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [isBusy, setIsBusy] = useState(false);

  const save = async () => {
    if (!level) {
      setError('Pick a level.');
      return;
    }
    setIsBusy(true);
    setError('');
    try {
      onSaved(await reviewPersonSkill(personId, row.kind, row.id, level, note));
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Not saved. Please try again.');
    } finally {
      setIsBusy(false);
    }
  };

  const waiting = row.status === 'self';
  return (
    <ThemedView
      style={[styles.skillCard, { borderColor: waiting ? theme.primary : theme.backgroundSelected }]}
    >
      <ThemedText type="smallBold">{row.name}</ThemedText>
      <ThemedText type="small">{row.level ? t('Level {level} — {label}', { level: row.level ?? '', label: row.level_label }) : 'Not rated yet'}</ThemedText>
      <ThemedText type="small" themeColor={waiting ? 'primary' : 'textSecondary'}>
        {row.status === 'confirmed' ? t('Confirmed by {name}', { name: row.confirmed_by }) : waiting ? 'Self-rated — waiting for review' : ''}
      </ThemedText>
      {row.note ? <ThemedText type="small" themeColor="textSecondary">“{row.note}”</ThemedText> : null}
      {row.evidence_url ? (
        <TouchableOpacity onPress={() => Linking.openURL(row.evidence_url)}>
          <ThemedText type="small" themeColor="primary">View evidence</ThemedText>
        </TouchableOpacity>
      ) : null}
      {canReview ? (
        <TouchableOpacity style={[styles.chip, { borderColor: theme.primary }]} onPress={onToggle}>
          <ThemedText type="small" themeColor="primary">
            {isOpen ? 'Cancel' : row.status === 'confirmed' ? 'Change level' : 'Confirm level'}
          </ThemedText>
        </TouchableOpacity>
      ) : null}
      {isOpen ? (
        <ThemedView style={styles.form}>
          {levels.map((option) => {
            const on = level === option.level;
            return (
              <TouchableOpacity
                key={option.level}
                style={[styles.levelOption, { borderColor: on ? theme.primary : theme.backgroundSelected },
                  on && { backgroundColor: theme.primary }]}
                onPress={() => setLevel(option.level)}
              >
                <ThemedText type="smallBold" style={on ? { color: theme.onPrimary } : undefined}>
                  {option.level} — {option.short}
                </ThemedText>
                <ThemedText type="small" style={{ color: on ? theme.onPrimary : theme.textSecondary }}>
                  {option.text}
                </ThemedText>
              </TouchableOpacity>
            );
          })}
          <TextInput
            style={[styles.input, { color: theme.text, borderColor: theme.backgroundSelected }]}
            value={note} onChangeText={setNote} maxLength={255}
            placeholder="Note (optional)" placeholderTextColor={theme.textSecondary}
          />
          {error ? <ThemedText type="small" themeColor="danger">{error}</ThemedText> : null}
          <TouchableOpacity
            style={[styles.saveButton, { backgroundColor: theme.primary }]} onPress={save} disabled={isBusy}
          >
            {isBusy ? <ActivityIndicator color={theme.onPrimary} /> : (
              <ThemedText type="smallBold" style={{ color: theme.onPrimary }}>Save level</ThemedText>
            )}
          </TouchableOpacity>
        </ThemedView>
      ) : null}
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: Spacing.four },
  content: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.six, gap: Spacing.three },
  header: { alignItems: 'center', gap: Spacing.one, backgroundColor: 'transparent' },
  photo: { width: 88, height: 88, borderRadius: 44 },
  noPhoto: { alignItems: 'center', justifyContent: 'center' },
  initials: { fontSize: 30, fontWeight: '700' },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  action: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.one, borderWidth: 1, borderRadius: Spacing.two,
    paddingHorizontal: Spacing.three, paddingVertical: Spacing.one,
  },
  card: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  chip: {
    alignSelf: 'flex-start', borderWidth: 1, borderRadius: Spacing.two, borderColor: 'transparent',
    paddingHorizontal: Spacing.three, paddingVertical: Spacing.one, marginTop: Spacing.one,
  },
  section: { gap: Spacing.two, backgroundColor: 'transparent' },
  skillCard: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  form: { gap: Spacing.two, marginTop: Spacing.two, backgroundColor: 'transparent' },
  levelOption: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, gap: 2 },
  input: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two },
  saveButton: { borderRadius: Spacing.two, padding: Spacing.three, alignItems: 'center' },
});
