import { useCallback, useRef, useState } from 'react';
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import {
  ActivityIndicator, Alert, KeyboardAvoidingView, Platform, ScrollView, StyleSheet, TextInput, TouchableOpacity,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import SignatureCanvas, { type SignatureViewRef } from 'react-native-signature-canvas';

import { ApiRequestError } from '@/api/client';
import { fetchMyReport, fetchMyTaskDetail, fetchParts, submitMyReport } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import {
  isNetworkError, pendingReportFor, removePendingReport, savePendingReport, withOfflineCopy,
} from '@/offline';
import type { CataloguePart, TaskDetail, WorkReport } from '@/types';

// Text fields only — quantity/cost are typed as text and sent as-is; the
// API (PartUsedItemForm) does the number validation, same as the web form.
interface PartRow {
  part_code: string;
  description: string;
  quantity: string;
  unit_cost: string;
  currency_code: string;
}

type FieldErrors = Record<string, string>;

// Hides the library's own Clear/Confirm footer — this screen has its own
// buttons, and reads the signature itself when the report is submitted.
const SIGNATURE_WEB_STYLE = `
  .m-signature-pad { box-shadow: none; border: none; margin: 0; }
  .m-signature-pad--body { border: none; }
  .m-signature-pad--footer { display: none; margin: 0; }
  body, html { height: 100%; }
`;

function emptyPart(currency: string): PartRow {
  return { part_code: '', description: '', quantity: '', unit_cost: '', currency_code: currency };
}

/** DRF's {"field": ["message"]} errors, flattened to one message per field. */
function fieldErrorsFrom(body: unknown): FieldErrors {
  const errors: FieldErrors = {};
  if (!body || typeof body !== 'object') {
    return errors;
  }
  for (const [field, value] of Object.entries(body as Record<string, unknown>)) {
    if (field === 'parts' && Array.isArray(value)) {
      const first = value.find((rowErrors) => rowErrors && Object.keys(rowErrors).length > 0);
      if (first) {
        const messages = Object.values(first as Record<string, string[]>).flat();
        errors.parts = messages[0] ?? 'Check the parts rows.';
      }
    } else if (Array.isArray(value)) {
      errors[field] = String(value[0]);
    }
  }
  return errors;
}

export default function ReportScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const theme = useTheme();
  const signatureRef = useRef<SignatureViewRef>(null);

  const [task, setTask] = useState<TaskDetail | null>(null);
  const [findings, setFindings] = useState('');
  const [actionTaken, setActionTaken] = useState('');
  const [resolved, setResolved] = useState(true);
  const [labourHours, setLabourHours] = useState('');
  const [customerName, setCustomerName] = useState('');
  const [parts, setParts] = useState<PartRow[]>([]);
  // The parts catalogue (Parts screen on the web); once it has anything in
  // it, the API only accepts codes from it — so suggest them as they type.
  const [catalogue, setCatalogue] = useState<CataloguePart[]>([]);
  const [signatureOnFile, setSignatureOnFile] = useState(false);
  // Signing happens on its own screen (see the `signing` branch below) so
  // the customer never sees labour hours or part prices while signing.
  const [signing, setSigning] = useState(false);
  const [newSignature, setNewSignature] = useState<string | null>(null);
  const [signError, setSignError] = useState('');
  const [scrollEnabled, setScrollEnabled] = useState(true);
  const [isBusy, setIsBusy] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [errors, setErrors] = useState<FieldErrors>({});
  const [isOffline, setIsOffline] = useState(false);

  const load = useCallback(async () => {
    try {
      // With no signal, each falls back to the copy kept from the last time
      // it loaded (src/offline.ts), so a report can still be filled in.
      const [detail, reportResult, catalogueResult] = await Promise.all([
        withOfflineCopy(`task-${id}`, () => fetchMyTaskDetail(Number(id))),
        withOfflineCopy(`report-${id}`, () => fetchMyReport(Number(id))),
        withOfflineCopy('parts', fetchParts),
      ]);
      setTask(detail.data);
      setCatalogue(catalogueResult.data);
      setIsOffline(detail.offline || reportResult.offline);
      const report: WorkReport | null = reportResult.data.report;
      const pending = pendingReportFor(Number(id));
      if (pending) {
        // A report already filled in here but not sent yet — carry on from it.
        const input = pending.input;
        setFindings(input.findings);
        setActionTaken(input.action_taken);
        setResolved(input.resolved);
        setLabourHours(input.labour_hours);
        setCustomerName(input.customer_name);
        setNewSignature(input.signature);
        setSignatureOnFile(Boolean(report?.signature_url));
        setParts(input.parts.map((part) => ({
          ...part, quantity: String(part.quantity), unit_cost: String(part.unit_cost),
        })));
        if (pending.error) {
          setErrors({ form: `This report couldn’t be sent: ${pending.error} Fix it and submit again.` });
        }
      } else if (report) {
        setFindings(report.findings);
        setActionTaken(report.action_taken);
        setResolved(report.resolved);
        setLabourHours(report.labour_hours);
        setCustomerName(report.customer_name);
        setSignatureOnFile(Boolean(report.signature_url));
        setParts(report.parts_used.map((part) => ({
          ...part, quantity: String(part.quantity), unit_cost: String(part.unit_cost),
        })));
      }
    } catch (err) {
      setLoadError(err instanceof ApiRequestError ? err.message : 'Could not load this report.');
    }
  }, [id]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  // Up to five catalogue parts matching what's typed, hidden once it's an
  // exact catalogue code.
  const partSuggestions = (typed: string): CataloguePart[] => {
    const query = typed.trim().toUpperCase();
    if (!query || catalogue.some((part) => part.code === query)) {
      return [];
    }
    return catalogue
      .filter((part) => part.code.includes(query) || part.description.toUpperCase().includes(query))
      .slice(0, 5);
  };

  const updatePart = (index: number, field: keyof PartRow, value: string) => {
    setParts((rows) => rows.map((row, i) => (i === index ? { ...row, [field]: value } : row)));
  };

  const submit = async () => {
    setIsBusy(true);
    setErrors({});
    const input = {
      findings,
      action_taken: actionTaken,
      resolved,
      labour_hours: labourHours,
      customer_name: customerName,
      // Fully blank rows are just unused space, same as the web form.
      parts: parts
        .filter((row) => row.part_code || row.quantity || row.unit_cost)
        .map((row) => ({ ...row, quantity: Number(row.quantity) || 0 })),
      signature: newSignature,
    };
    try {
      const result = await submitMyReport(Number(id), input);
      removePendingReport(Number(id));
      Alert.alert('Report saved', result.message);
      router.back();
    } catch (err) {
      if (isNetworkError(err) && task) {
        savePendingReport(Number(id), task.task_number, input);
        Alert.alert(
          'Saved on this phone',
          'No connection right now. The report will be sent automatically when you’re back online.',
        );
        router.back();
      } else if (err instanceof ApiRequestError && err.status === 400) {
        const fieldErrors = fieldErrorsFrom(err.body);
        setErrors(Object.keys(fieldErrors).length ? fieldErrors : { form: err.message });
      } else {
        setErrors({ form: err instanceof ApiRequestError ? err.message : 'Could not save — try again.' });
      }
    } finally {
      setIsBusy(false);
    }
  };

  // The pad only hands its image back through onOK/onEmpty, so "Done"
  // asks for it and the answer arrives there.
  const finishSigning = () => {
    setSignError('');
    signatureRef.current?.readSignature();
  };

  if (!task) {
    return (
      <ThemedView style={styles.centered}>
        {loadError ? <ThemedText themeColor="danger">{loadError}</ThemedText> : <ActivityIndicator />}
      </ThemedView>
    );
  }

  const inputStyle = [styles.input, { color: theme.text, borderColor: theme.backgroundSelected }];

  if (signing) {
    const usedParts = parts.filter((row) => row.part_code);
    return (
      <ThemedView style={styles.container}>
        <SafeAreaView style={styles.safeArea} edges={['top']}>
          <ScrollView contentContainerStyle={styles.content} scrollEnabled={scrollEnabled}>
            <ThemedText type="title" style={styles.title}>Please review and sign</ThemedText>
            <ThemedText themeColor="textSecondary">{task.task_number} — {task.customer_name}</ThemedText>

            <ThemedView style={[styles.summary, { backgroundColor: theme.backgroundElement }]}>
              <ThemedText type="smallBold">What we found</ThemedText>
              <ThemedText>{findings || '—'}</ThemedText>
              {actionTaken ? (
                <>
                  <ThemedText type="smallBold">What we did</ThemedText>
                  <ThemedText>{actionTaken}</ThemedText>
                </>
              ) : null}
              <ThemedText type="smallBold">Problem resolved</ThemedText>
              <ThemedText>{resolved ? 'Yes' : 'No'}</ThemedText>
              {usedParts.length > 0 ? (
                <>
                  <ThemedText type="smallBold">Parts used</ThemedText>
                  {usedParts.map((row, index) => (
                    <ThemedText key={index}>
                      {row.quantity || '1'} × {row.part_code}{row.description ? ` — ${row.description}` : ''}
                    </ThemedText>
                  ))}
                </>
              ) : null}
            </ThemedView>

            <Field label="Your name" error={errors.customer_name}>
              <TextInput style={inputStyle} value={customerName} onChangeText={setCustomerName} />
            </Field>

            <ThemedView style={styles.section}>
              <ThemedText type="smallBold">Your signature</ThemedText>
              <ThemedView style={[styles.signatureBox, { borderColor: theme.backgroundSelected }]}>
                <SignatureCanvas
                  ref={signatureRef}
                  webStyle={SIGNATURE_WEB_STYLE}
                  backgroundColor="#ffffff"
                  penColor="#000000"
                  imageType="image/png"
                  onBegin={() => setScrollEnabled(false)}
                  onEnd={() => setScrollEnabled(true)}
                  onOK={(signature) => {
                    setNewSignature(signature);
                    setSigning(false);
                  }}
                  onEmpty={() => setSignError('Please sign in the box above.')}
                />
              </ThemedView>
              <TouchableOpacity onPress={() => signatureRef.current?.clearSignature()}>
                <ThemedText themeColor="primary" type="small">Clear signature</ThemedText>
              </TouchableOpacity>
              {signError ? <ThemedText themeColor="danger" type="small">{signError}</ThemedText> : null}
            </ThemedView>

            <TouchableOpacity style={[styles.button, { backgroundColor: theme.primary }]} onPress={finishSigning}>
              <ThemedText style={styles.buttonText}>Done — hand back to the technician</ThemedText>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => setSigning(false)} style={styles.centerLink}>
              <ThemedText themeColor="textSecondary">Cancel</ThemedText>
            </TouchableOpacity>
          </ScrollView>
        </SafeAreaView>
      </ThemedView>
    );
  }

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backLink}>
          <ThemedText themeColor="primary">← Back</ThemedText>
        </TouchableOpacity>

        <KeyboardAvoidingView style={styles.safeArea} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <ScrollView contentContainerStyle={styles.content} scrollEnabled={scrollEnabled}>
            <ThemedText type="title" style={styles.title}>Work report</ThemedText>
            <ThemedText themeColor="textSecondary">{task.task_number} — {task.customer_name}</ThemedText>
            {isOffline ? (
              <ThemedText themeColor="danger" type="small">
                No connection — you can still fill this in; it’ll be sent once you’re back online.
              </ThemedText>
            ) : null}

            <Field label="What did you find?" error={errors.findings}>
              <TextInput style={[inputStyle, styles.multiline]} value={findings} onChangeText={setFindings} multiline />
            </Field>

            <Field label="What did you do?" error={errors.action_taken}>
              <TextInput
                style={[inputStyle, styles.multiline]} value={actionTaken} onChangeText={setActionTaken} multiline
              />
            </Field>

            <Field label="Is the problem resolved?" error={errors.resolved}>
              <ThemedView style={styles.toggleRow}>
                {[true, false].map((value) => (
                  <TouchableOpacity
                    key={String(value)}
                    style={[
                      styles.toggle, { borderColor: theme.backgroundSelected },
                      resolved === value && { backgroundColor: theme.primary, borderColor: theme.primary },
                    ]}
                    onPress={() => setResolved(value)}
                  >
                    <ThemedText style={resolved === value ? styles.toggleTextActive : undefined}>
                      {value ? 'Yes' : 'No'}
                    </ThemedText>
                  </TouchableOpacity>
                ))}
              </ThemedView>
            </Field>

            <Field label="Labour hours" error={errors.labour_hours}>
              <TextInput
                style={inputStyle} value={labourHours} onChangeText={setLabourHours}
                keyboardType="decimal-pad" placeholder="e.g. 2.5" placeholderTextColor={theme.textSecondary}
              />
            </Field>

            <ThemedView style={styles.section}>
              <ThemedText type="smallBold">Parts used</ThemedText>
              {parts.map((row, index) => (
                <ThemedView key={index} style={[styles.partCard, { borderColor: theme.backgroundSelected }]}>
                  <TextInput
                    style={inputStyle} value={row.part_code} placeholder="Part code"
                    placeholderTextColor={theme.textSecondary} autoCapitalize="characters"
                    onChangeText={(value) => updatePart(index, 'part_code', value)}
                  />
                  {partSuggestions(row.part_code).map((part) => (
                    <TouchableOpacity
                      key={part.code}
                      style={[styles.suggestion, { borderColor: theme.backgroundSelected }]}
                      onPress={() => {
                        updatePart(index, 'part_code', part.code);
                        if (!row.description) {
                          updatePart(index, 'description', part.description);
                        }
                      }}
                    >
                      <ThemedText type="small">
                        <ThemedText type="smallBold">{part.code}</ThemedText>
                        {part.description ? ` — ${part.description}` : ''}
                      </ThemedText>
                    </TouchableOpacity>
                  ))}
                  <TextInput
                    style={inputStyle} value={row.description} placeholder="Description"
                    placeholderTextColor={theme.textSecondary}
                    onChangeText={(value) => updatePart(index, 'description', value)}
                  />
                  <ThemedView style={styles.partNumbers}>
                    <TextInput
                      style={[inputStyle, styles.partNumber]} value={row.quantity} placeholder="Qty"
                      placeholderTextColor={theme.textSecondary} keyboardType="number-pad"
                      onChangeText={(value) => updatePart(index, 'quantity', value)}
                    />
                    <TextInput
                      style={[inputStyle, styles.partNumber]} value={row.unit_cost} placeholder="Unit cost"
                      placeholderTextColor={theme.textSecondary} keyboardType="decimal-pad"
                      onChangeText={(value) => updatePart(index, 'unit_cost', value)}
                    />
                    <TextInput
                      style={[inputStyle, styles.partCurrency]} value={row.currency_code} placeholder="AED"
                      placeholderTextColor={theme.textSecondary} autoCapitalize="characters" maxLength={3}
                      onChangeText={(value) => updatePart(index, 'currency_code', value.toUpperCase())}
                    />
                  </ThemedView>
                  <TouchableOpacity onPress={() => setParts((rows) => rows.filter((_, i) => i !== index))}>
                    <ThemedText themeColor="danger" type="small">Remove part</ThemedText>
                  </TouchableOpacity>
                </ThemedView>
              ))}
              {errors.parts ? <ThemedText themeColor="danger" type="small">{errors.parts}</ThemedText> : null}
              <TouchableOpacity
                style={[styles.secondaryButton, { borderColor: theme.primary }]}
                onPress={() => setParts((rows) => [...rows, emptyPart(task.currency_code)])}
              >
                <ThemedText themeColor="primary">+ Add a part</ThemedText>
              </TouchableOpacity>
            </ThemedView>

            <ThemedView style={styles.section}>
              <ThemedText type="smallBold">
                Customer sign-off{task.requires_signature ? ' (required)' : ''}
              </ThemedText>
              <ThemedText themeColor="textSecondary" type="small">
                {newSignature
                  ? `Signed by ${customerName || 'the customer'} ✓`
                  : signatureOnFile
                    ? `Signed by ${customerName || 'the customer'} — sign again only to replace it.`
                    : 'The customer reviews the work and signs on their own screen — they won’t see hours or prices.'}
              </ThemedText>
              <TouchableOpacity
                style={[styles.secondaryButton, { borderColor: theme.primary }]}
                onPress={() => {
                  setSignError('');
                  setSigning(true);
                }}
              >
                <ThemedText themeColor="primary">
                  {newSignature || signatureOnFile ? 'Sign again' : 'Hand to customer to sign'}
                </ThemedText>
              </TouchableOpacity>
              {errors.customer_name ? (
                <ThemedText themeColor="danger" type="small">{errors.customer_name}</ThemedText>
              ) : null}
              {errors.signature ? <ThemedText themeColor="danger" type="small">{errors.signature}</ThemedText> : null}
            </ThemedView>

            {errors.form ? <ThemedText themeColor="danger" style={styles.formError}>{errors.form}</ThemedText> : null}

            <TouchableOpacity
              style={[styles.button, { backgroundColor: theme.primary }, isBusy && styles.buttonDisabled]}
              onPress={submit}
              disabled={isBusy}
            >
              {isBusy ? <ActivityIndicator color="#fff" /> : (
                <ThemedText style={styles.buttonText}>Submit report</ThemedText>
              )}
            </TouchableOpacity>
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </ThemedView>
  );
}

function Field({ label, error, children }: { label: string; error?: string; children: React.ReactNode }) {
  return (
    <ThemedView style={styles.section}>
      <ThemedText type="smallBold">{label}</ThemedText>
      {children}
      {error ? <ThemedText themeColor="danger" type="small">{error}</ThemedText> : null}
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  backLink: { paddingHorizontal: Spacing.three, paddingVertical: Spacing.two },
  content: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.six, gap: Spacing.three },
  title: { fontSize: 24, marginBottom: 0 },
  section: { gap: Spacing.two, backgroundColor: 'transparent' },
  input: {
    borderWidth: 1, borderRadius: Spacing.two, paddingHorizontal: Spacing.three, paddingVertical: Spacing.two,
    fontSize: 16,
  },
  multiline: { minHeight: 80, textAlignVertical: 'top' },
  toggleRow: { flexDirection: 'row', gap: Spacing.two, backgroundColor: 'transparent' },
  toggle: {
    flex: 1, borderWidth: 1, borderRadius: Spacing.two, paddingVertical: Spacing.two, alignItems: 'center',
  },
  toggleTextActive: { color: '#fff', fontWeight: '600' },
  suggestion: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two },
  partCard: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, gap: Spacing.two },
  partNumbers: { flexDirection: 'row', gap: Spacing.two, backgroundColor: 'transparent' },
  partNumber: { flex: 1 },
  partCurrency: { width: 72 },
  secondaryButton: { borderWidth: 1, borderRadius: Spacing.two, paddingVertical: Spacing.two, alignItems: 'center' },
  summary: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  centerLink: { alignItems: 'center', paddingVertical: Spacing.two },
  signatureBox: { height: 200, borderWidth: 1, borderRadius: Spacing.two, overflow: 'hidden' },
  formError: { textAlign: 'center' },
  button: { borderRadius: Spacing.two, paddingVertical: Spacing.three, alignItems: 'center' },
  buttonDisabled: { opacity: 0.6 },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '600' },
});
