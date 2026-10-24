import Ionicons from '@expo/vector-icons/Ionicons';
import * as ImagePicker from 'expo-image-picker';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import {
  ActivityIndicator, Alert, Linking, RefreshControl, ScrollView, StyleSheet, TextInput, TouchableOpacity,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { fetchMySkills, rateMySkill } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { MySkills, SkillRow } from '@/types';

type Evidence = { uri: string; name: string; type: string };

/**
 * Profile > My skills — the website's My skills page: progress toward
 * certification, what the levels mean, and each basic / advanced skill and
 * conduct area. Unrated or still waiting for review, it can be rated here:
 * a level, and for a skill a photo or video of doing the repair. Once a
 * supervisor confirms it, only a supervisor can change it.
 */
export default function MySkillsScreen() {
  const router = useRouter();
  const theme = useTheme();
  const [skills, setSkills] = useState<MySkills | null>(null);
  const [loadError, setLoadError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [showLevels, setShowLevels] = useState(false);
  // The one row whose rating form is open: "skill-3", "conduct-1".
  const [open, setOpen] = useState('');

  const load = useCallback(async () => {
    try {
      setSkills(await fetchMySkills());
      setLoadError('');
    } catch (err) {
      setLoadError(err instanceof ApiRequestError ? err.message : 'Could not load your skills.');
    }
  }, []);

  useFocusEffect(useCallback(() => {
    load();
  }, [load]));

  const refresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  if (!skills) {
    return (
      <ThemedView style={styles.centered}>
        {loadError ? <ThemedText themeColor="danger">{loadError}</ThemedText> : <ActivityIndicator />}
      </ThemedView>
    );
  }

  const { progress } = skills;
  const figure = (value: string, label: string) => (
    <ThemedView style={[styles.figure, { backgroundColor: theme.backgroundElement }]}>
      <ThemedText type="subtitle">{value}</ThemedText>
      <ThemedText type="small" themeColor="textSecondary">{label}</ThemedText>
    </ThemedView>
  );

  const group = (title: string, rows: SkillRow[], hint?: string) => (
    rows.length ? (
      <ThemedView style={styles.section}>
        <ThemedText type="subtitle">{title}</ThemedText>
        {hint ? <ThemedText type="small" themeColor="textSecondary">{hint}</ThemedText> : null}
        {rows.map((row) => (
          <SkillCard
            key={`${row.kind}-${row.id}`} row={row} levels={skills.levels}
            isOpen={open === `${row.kind}-${row.id}`}
            onToggle={() => setOpen(open === `${row.kind}-${row.id}` ? '' : `${row.kind}-${row.id}`)}
            onSaved={(updated) => {
              setSkills(updated);
              setOpen('');
            }}
          />
        ))}
      </ThemedView>
    ) : null
  );

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <ScrollView
          contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled"
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} />}
        >
          <TouchableOpacity onPress={() => router.navigate('/profile')}>
            <ThemedText themeColor="textSecondary">‹ Profile</ThemedText>
          </TouchableOpacity>
          <ThemedText type="title">My skills</ThemedText>
          <ThemedText type="small" themeColor="textSecondary">
            Rate yourself on each skill with a photo or short video of you doing the repair. Your supervisor
            checks it and confirms or adjusts the level; after that, only a supervisor can change it.
          </ThemedText>

          <ThemedView style={styles.figures}>
            {figure(`${progress.basic_confirmed}/${progress.basic_total}`, 'Basic confirmed')}
            {figure(`${progress.advanced_confirmed}/${progress.advanced_total}`, 'Advanced confirmed')}
            {figure(`${progress.conduct_confirmed}/${progress.conduct_total}`, 'Conduct confirmed')}
            {figure(String(progress.waiting), 'Waiting for review')}
          </ThemedView>
          {progress.is_certified ? (
            <ThemedView style={[styles.certified, { borderColor: theme.primary }]}>
              <Ionicons name="ribbon-outline" size={18} color={theme.primary} />
              <ThemedText themeColor="primary" type="smallBold">Certified</ThemedText>
            </ThemedView>
          ) : (
            <ThemedText type="small" themeColor="textSecondary">
              Certification needs every basic skill and every conduct area confirmed by a supervisor at level{' '}
              {progress.reliable_level} or higher.
            </ThemedText>
          )}

          <TouchableOpacity onPress={() => setShowLevels(!showLevels)} style={styles.levelsToggle}>
            <Ionicons name={showLevels ? 'chevron-down' : 'chevron-forward'} size={16} color={theme.primary} />
            <ThemedText themeColor="primary">What the levels mean</ThemedText>
          </TouchableOpacity>
          {showLevels ? skills.levels.map((level) => (
            <ThemedView key={level.level} style={styles.levelLine}>
              <ThemedText type="smallBold">
                Level {level.level}{level.level === progress.reliable_level ? ' · needed for certification' : ''}
              </ThemedText>
              <ThemedText type="small">{level.text}</ThemedText>
            </ThemedView>
          )) : null}

          {group('Basic level', skills.basic)}
          {group('Advanced level', skills.advanced, 'Not required for certification; an additional advanced skill set.')}
          {group('Conduct', skills.conduct)}
        </ScrollView>
      </SafeAreaView>
    </ThemedView>
  );
}

/** One skill or conduct area: its level and status, and the rating form. */
function SkillCard({ row, levels, isOpen, onToggle, onSaved }: {
  row: SkillRow; levels: MySkills['levels']; isOpen: boolean; onToggle: () => void; onSaved: (skills: MySkills) => void;
}) {
  const theme = useTheme();
  const [level, setLevel] = useState<number | null>(row.status === 'self' ? row.level : null);
  const [note, setNote] = useState('');
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [error, setError] = useState('');
  const [isBusy, setIsBusy] = useState(false);

  const pickEvidence = async (fromCamera: boolean) => {
    const permission = fromCamera
      ? await ImagePicker.requestCameraPermissionsAsync()
      : await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) {
      Alert.alert('Permission required', fromCamera ? 'Camera access is needed.' : 'Photo library access is needed.');
      return;
    }
    const options: ImagePicker.ImagePickerOptions = { mediaTypes: ['images', 'videos'], quality: 0.8 };
    const result = fromCamera
      ? await ImagePicker.launchCameraAsync(options)
      : await ImagePicker.launchImageLibraryAsync(options);
    const asset = result.canceled ? null : result.assets[0];
    if (asset) {
      setEvidence({
        uri: asset.uri,
        name: asset.fileName ?? (asset.type === 'video' ? 'evidence.mp4' : 'evidence.jpg'),
        type: asset.mimeType ?? (asset.type === 'video' ? 'video/mp4' : 'image/jpeg'),
      });
    }
  };

  const save = async () => {
    if (!level) {
      setError('Pick a level.');
      return;
    }
    if (row.kind === 'skill' && !evidence) {
      setError('Add a photo or video of you doing this repair.');
      return;
    }
    setIsBusy(true);
    setError('');
    try {
      onSaved(await rateMySkill(row.kind, row.id, level, note, evidence ?? undefined));
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Not saved. Please try again.');
    } finally {
      setIsBusy(false);
    }
  };

  const status = {
    none: { text: 'Not rated yet', color: theme.textSecondary },
    self: { text: 'Waiting for supervisor review', color: theme.textSecondary },
    confirmed: { text: `Confirmed by ${row.confirmed_by}`, color: theme.primary },
  }[row.status];

  return (
    <ThemedView style={[styles.card, { borderColor: theme.backgroundSelected }]}>
      <ThemedText type="smallBold">{row.name}</ThemedText>
      <ThemedText type="small">{row.level ? `Level ${row.level} — ${row.level_label}` : '—'}</ThemedText>
      <ThemedText type="small" style={{ color: status.color }}>{status.text}</ThemedText>
      {row.status === 'confirmed' && row.note ? (
        <ThemedText type="small" themeColor="textSecondary">{row.note}</ThemedText>
      ) : null}
      {row.evidence_url ? (
        <TouchableOpacity onPress={() => Linking.openURL(row.evidence_url)}>
          <ThemedText type="small" themeColor="primary">View evidence</ThemedText>
        </TouchableOpacity>
      ) : null}

      {row.can_rate ? (
        <TouchableOpacity style={[styles.rateButton, { borderColor: theme.primary }]} onPress={onToggle}>
          <ThemedText type="small" themeColor="primary">
            {isOpen ? 'Cancel' : row.status === 'self' ? 'Change my rating' : 'Rate'}
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
                <ThemedText type="small" style={on ? { color: theme.onPrimary } : { color: theme.textSecondary }}>
                  {option.text}
                </ThemedText>
              </TouchableOpacity>
            );
          })}
          {row.kind === 'skill' ? (
            <>
              <ThemedView style={styles.evidenceRow}>
                <TouchableOpacity
                  style={[styles.rateButton, { borderColor: theme.primary }]} onPress={() => pickEvidence(true)}
                >
                  <ThemedText type="small" themeColor="primary">Take photo or video</ThemedText>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.rateButton, { borderColor: theme.primary }]} onPress={() => pickEvidence(false)}
                >
                  <ThemedText type="small" themeColor="primary">Choose from gallery</ThemedText>
                </TouchableOpacity>
              </ThemedView>
              <ThemedText type="small" themeColor={evidence ? 'text' : 'textSecondary'}>
                {evidence ? `✓ ${evidence.name}` : 'A photo or video of you doing this repair is needed.'}
              </ThemedText>
              <TextInput
                style={[styles.input, { color: theme.text, borderColor: theme.backgroundSelected }]}
                value={note} onChangeText={setNote} maxLength={255}
                placeholder="Note (optional)" placeholderTextColor={theme.textSecondary}
              />
            </>
          ) : null}
          {error ? <ThemedText type="small" themeColor="danger">{error}</ThemedText> : null}
          <TouchableOpacity
            style={[styles.saveButton, { backgroundColor: theme.primary }]} onPress={save} disabled={isBusy}
          >
            {isBusy ? <ActivityIndicator color={theme.onPrimary} /> : (
              <ThemedText type="smallBold" style={{ color: theme.onPrimary }}>Save my rating</ThemedText>
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
  figures: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  figure: { flexBasis: '47%', flexGrow: 1, borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  certified: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.one, alignSelf: 'flex-start',
    borderWidth: 1, borderRadius: 999, paddingHorizontal: Spacing.three, paddingVertical: Spacing.one,
  },
  levelsToggle: { flexDirection: 'row', alignItems: 'center', gap: Spacing.one },
  levelLine: { gap: 2, backgroundColor: 'transparent' },
  section: { gap: Spacing.two, backgroundColor: 'transparent' },
  card: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  rateButton: {
    alignSelf: 'flex-start', borderWidth: 1, borderRadius: Spacing.two,
    paddingHorizontal: Spacing.three, paddingVertical: Spacing.one, marginTop: Spacing.one,
  },
  form: { gap: Spacing.two, marginTop: Spacing.two, backgroundColor: 'transparent' },
  levelOption: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, gap: 2 },
  evidenceRow: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  input: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two },
  saveButton: { borderRadius: Spacing.two, padding: Spacing.three, alignItems: 'center' },
});
