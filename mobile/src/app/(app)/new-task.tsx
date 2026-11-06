import DateTimePicker, { type DateTimePickerEvent } from '@react-native-community/datetimepicker';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import {
  ActivityIndicator, Alert, KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Switch, TextInput,
  TouchableOpacity,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { createTask, fetchNewTaskForm } from '@/api/endpoints';
import { ChoicePicker } from '@/components/choice-picker';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { FormFieldSpec } from '@/types';

/** "2026-10-25T09:30" (the server's local format) <-> a Date, and back. */
function toLocalValue(date: Date) {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
function fromLocalValue(value: string) {
  return value ? new Date(value) : new Date();
}
function shown(value: string) {
  return value ? value.replace('T', '  ') : '';
}

/**
 * Team → New task — the website's New task, same form and rules: the
 * site, what kind of job and brand, the skill it needs, what's wrong,
 * priority, how it came in, billing, when it was reported, when it's
 * booked, the estimate and the responsible supervisor. Then it opens on
 * its page to assign the team. Adding a new site, brand or task type is
 * desk work on the website.
 */
export default function NewTaskScreen() {
  const router = useRouter();
  const theme = useTheme();
  const [fields, setFields] = useState<Record<string, FormFieldSpec> | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [warranty, setWarranty] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loadError, setLoadError] = useState('');
  const [isBusy, setIsBusy] = useState(false);
  // Which date/time is being picked, and in which step (Android picks the date, then the time).
  const [picking, setPicking] = useState<{ field: string; mode: 'date' | 'time' } | null>(null);

  useFocusEffect(useCallback(() => {
    fetchNewTaskForm().then(({ fields: specs }) => {
      setFields(specs);
      setValues(Object.fromEntries(Object.entries(specs).map(([name, spec]) => [name, spec.initial])));
      setWarranty(specs.is_warranty?.initial === 'True');
      setErrors({});
    }).catch((err) => {
      setLoadError(err instanceof ApiRequestError ? err.message : 'Could not load the form.');
    });
  }, []));

  if (!fields) {
    return (
      <ThemedView style={styles.centered}>
        {loadError ? <ThemedText themeColor="danger">{loadError}</ThemedText> : <ActivityIndicator />}
      </ThemedView>
    );
  }

  const set = (name: string) => (value: string) => setValues((current) => ({ ...current, [name]: value }));
  const inputStyle = [styles.input, { color: theme.text, borderColor: theme.backgroundSelected }];

  const picker = (name: string, optional = false, placeholder?: string) => (
    <ChoicePicker
      label={`${fields[name].label}${fields[name].required ? '' : ' (optional)'}`}
      choices={fields[name].choices ?? []} value={values[name] ?? ''} onChange={set(name)}
      optional={optional} error={errors[name]} placeholder={placeholder}
    />
  );

  const chips = (name: string) => (
    <ThemedView style={styles.field}>
      <ThemedText type="smallBold">{fields[name].label}</ThemedText>
      <ThemedView style={styles.chips}>
        {(fields[name].choices ?? []).map((choice) => {
          const on = values[name] === choice.value;
          return (
            <TouchableOpacity
              key={choice.value}
              style={[styles.chip, { borderColor: on ? theme.primary : theme.backgroundSelected },
                on && { backgroundColor: theme.primary }]}
              onPress={() => set(name)(on && !fields[name].required ? '' : choice.value)}
            >
              <ThemedText type="small" style={on ? { color: theme.onPrimary } : undefined}>{choice.label}</ThemedText>
            </TouchableOpacity>
          );
        })}
      </ThemedView>
      {errors[name] ? <ThemedText type="small" themeColor="danger">{errors[name]}</ThemedText> : null}
    </ThemedView>
  );

  const when = (name: string, hint?: string) => (
    <ThemedView style={styles.field}>
      <ThemedText type="smallBold">{fields[name].label}{fields[name].required ? '' : ' (optional)'}</ThemedText>
      <ThemedView style={styles.chips}>
        <TouchableOpacity
          style={[styles.dateButton, { borderColor: errors[name] ? theme.danger : theme.backgroundSelected }]}
          onPress={() => setPicking({ field: name, mode: 'date' })}
        >
          <ThemedText themeColor={values[name] ? 'text' : 'textSecondary'}>
            {values[name] ? shown(values[name]) : 'Pick a date and time'}
          </ThemedText>
        </TouchableOpacity>
        {values[name] && !fields[name].required ? (
          <TouchableOpacity style={styles.chip} onPress={() => set(name)('')}>
            <ThemedText type="small" themeColor="textSecondary">Clear</ThemedText>
          </TouchableOpacity>
        ) : null}
      </ThemedView>
      {hint ? <ThemedText type="small" themeColor="textSecondary">{hint}</ThemedText> : null}
      {errors[name] ? <ThemedText type="small" themeColor="danger">{errors[name]}</ThemedText> : null}
    </ThemedView>
  );

  const onPicked = (event: DateTimePickerEvent, date?: Date) => {
    if (!picking) {
      return;
    }
    if (event.type === 'dismissed' || !date) {
      setPicking(null);
      return;
    }
    const merged = fromLocalValue(values[picking.field]);
    if (picking.mode === 'date') {
      merged.setFullYear(date.getFullYear(), date.getMonth(), date.getDate());
    } else {
      merged.setHours(date.getHours(), date.getMinutes());
    }
    set(picking.field)(toLocalValue(merged));
    // Android shows the date and the time as two dialogs; iOS one picker.
    setPicking(Platform.OS === 'android' && picking.mode === 'date' ? { field: picking.field, mode: 'time' } : null);
  };

  const save = async () => {
    setIsBusy(true);
    setErrors({});
    try {
      const result = await createTask({ ...values, is_warranty: warranty });
      const open = () => router.replace(`/team-task/${result.id}`);
      if (result.warning) {
        Alert.alert(`Task ${result.task_number} created`, result.warning, [{ text: 'OK', onPress: open }]);
      } else {
        open();
      }
    } catch (err) {
      if (err instanceof ApiRequestError && err.status === 400 && err.body && typeof err.body === 'object') {
        const body = err.body as Record<string, string[]>;
        setErrors(Object.fromEntries(Object.entries(body).map(([field, messages]) => [
          field === '__all__' ? 'form' : field, messages.join(' '),
        ])));
      } else {
        setErrors({ form: err instanceof ApiRequestError ? err.message : 'Not saved. Please try again.' });
      }
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.flex}>
          <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
            <TouchableOpacity onPress={() => router.back()}>
              <ThemedText themeColor="primary">← Back</ThemedText>
            </TouchableOpacity>
            <ThemedText type="title">New task</ThemedText>

            {picker('site', false, 'Customer — site')}
            {picker('task_type', true)}
            {picker('brand', true)}

            <ThemedView style={styles.field}>
              <ThemedText type="smallBold">{fields.description.label}</ThemedText>
              <TextInput
                style={[inputStyle, styles.multiline]} value={values.description ?? ''} onChangeText={set('description')}
                multiline placeholder="What's wrong, or what needs doing" placeholderTextColor={theme.textSecondary}
              />
              {errors.description ? <ThemedText type="small" themeColor="danger">{errors.description}</ThemedText> : null}
            </ThemedView>

            {chips('priority')}
            {chips('source')}
            {chips('billing_type')}
            <ThemedView style={styles.switchRow}>
              <ThemedText type="smallBold" style={styles.flex}>{fields.is_warranty.label}</ThemedText>
              <Switch value={warranty} onValueChange={setWarranty} />
            </ThemedView>

            {when('reported_at')}
            {when('scheduled_for', 'Leave empty to book the day later; the team still sees it as unscheduled.')}

            <ThemedView style={styles.field}>
              <ThemedText type="smallBold">{fields.estimated_hours.label} (optional)</ThemedText>
              <TextInput
                style={inputStyle} value={values.estimated_hours ?? ''} onChangeText={set('estimated_hours')}
                keyboardType="decimal-pad" placeholder="e.g. 2.5" placeholderTextColor={theme.textSecondary}
              />
              {errors.estimated_hours ? <ThemedText type="small" themeColor="danger">{errors.estimated_hours}</ThemedText> : null}
            </ThemedView>

            {picker('required_skill', true)}
            {values.required_skill ? chips('min_level') : null}
            {picker('responsible_supervisor', true)}

            {errors.form ? <ThemedText themeColor="danger">{errors.form}</ThemedText> : null}
            <TouchableOpacity
              style={[styles.saveButton, { backgroundColor: theme.primary }]} onPress={save} disabled={isBusy}
            >
              {isBusy ? <ActivityIndicator color={theme.onPrimary} /> : (
                <ThemedText type="smallBold" style={{ color: theme.onPrimary }}>Create task</ThemedText>
              )}
            </TouchableOpacity>
            <ThemedText type="small" themeColor="textSecondary">
              Next, it opens on its page to assign the team.
            </ThemedText>
          </ScrollView>
        </KeyboardAvoidingView>
        {picking ? (
          <DateTimePicker
            value={fromLocalValue(values[picking.field])}
            mode={Platform.OS === 'ios' ? 'datetime' : picking.mode}
            display={Platform.OS === 'ios' ? 'inline' : 'default'}
            onChange={onPicked}
          />
        ) : null}
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  flex: { flex: 1 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: Spacing.four },
  content: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.six, gap: Spacing.three },
  field: { gap: Spacing.one, backgroundColor: 'transparent' },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  chip: {
    borderWidth: 1, borderColor: 'transparent', borderRadius: 999,
    paddingHorizontal: Spacing.three, paddingVertical: Spacing.one,
  },
  dateButton: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three },
  switchRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two, backgroundColor: 'transparent' },
  input: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three, fontSize: 16 },
  multiline: { minHeight: 90, textAlignVertical: 'top' },
  saveButton: { borderRadius: Spacing.two, padding: Spacing.three, alignItems: 'center' },
});
