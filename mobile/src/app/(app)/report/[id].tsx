import { useCallback, useRef, useState } from 'react';
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import {
  ActivityIndicator, Alert, KeyboardAvoidingView, Linking, Platform, ScrollView, StyleSheet, Switch, TextInput, TouchableOpacity,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import SignatureCanvas, { type SignatureViewRef } from 'react-native-signature-canvas';
import { WebView } from 'react-native-webview';

import { API_BASE_URL, ApiRequestError } from '@/api/client';
import { fetchMyReport, fetchMyTaskDetail, fetchParts, previewMyReport, submitMyReport } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { currentLocation } from '@/location';
import {
  isNetworkError, pendingReportFor, removePendingReport, savePendingReport, withOfflineCopy,
} from '@/offline';
import type {
  Brand, CataloguePart, Choice, ReportHelper, ReportMachine, SiteMachine, TaskDetail, WorkReport,
} from '@/types';

// Text fields only — quantity/cost are typed as text and sent as-is; the
// API (PartUsedItemForm) does the number validation, same as the web form.
interface PartRow {
  part_code: string;
  description: string;
  quantity: string;
}

// One machine card: a machine at the site (asset_id set, name shown), or
// one to register, with its brand typed and picked from the list.
interface MachineRow extends ReportMachine {
  key: string;
  brand_name: string;
}

type FieldErrors = Record<string, string>;

let nextMachineKey = 0;

function machineRow(machine: ReportMachine, brands: Brand[]): MachineRow {
  nextMachineKey += 1;
  return {
    ...machine,
    key: String(nextMachineKey),
    brand_name: brands.find((brand) => brand.id === machine.brand_id)?.name ?? '',
  };
}

/** What the server is sent for a card, without the screen's own fields. */
function machineInput({ key, brand_name, name, ...machine }: MachineRow): ReportMachine {
  return machine;
}

// Hides the library's own Clear/Confirm footer — this screen has its own
// buttons, and reads the signature itself when the report is submitted.
const SIGNATURE_WEB_STYLE = `
  .m-signature-pad { box-shadow: none; border: none; margin: 0; }
  .m-signature-pad--body { border: none; }
  .m-signature-pad--footer { display: none; margin: 0; }
  body, html { height: 100%; }
`;

// No price: the managers agree prices with the customer before the visit.
function emptyPart(): PartRow {
  return { part_code: '', description: '', quantity: '' };
}

/** DRF's {"field": ["message"]} errors, flattened to one message per field. */
function fieldErrorsFrom(body: unknown): FieldErrors {
  const errors: FieldErrors = {};
  if (!body || typeof body !== 'object') {
    return errors;
  }
  for (const [field, value] of Object.entries(body as Record<string, unknown>)) {
    if ((field === 'parts' || field === 'machines') && Array.isArray(value)) {
      const first = value.find((rowErrors) => rowErrors && Object.keys(rowErrors).length > 0);
      if (first) {
        const messages = Object.values(first as Record<string, string[]>).flat();
        errors[field] = messages[0] ?? `Please review the ${field} entries.`;
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
  // The visit's time as "1:32", worked out by the server from the taps — shown, not typed.
  const [labourHours, setLabourHours] = useState('');
  const [timeIsFinal, setTimeIsFinal] = useState(false);
  const [customerName, setCustomerName] = useState('');
  const [parts, setParts] = useState<PartRow[]>([]);
  // The parts catalogue (Parts screen on the web); once it has anything in
  // it, the API only accepts codes from it — so suggest them as they type.
  const [catalogue, setCatalogue] = useState<CataloguePart[]>([]);
  const [signatureOnFile, setSignatureOnFile] = useState(false);
  // Signing happens on its own screen (see the `signing` branch below) so
  // the customer sees the work and the time on site while signing, not the form itself.
  const [signing, setSigning] = useState(false);
  const [newSignature, setNewSignature] = useState<string | null>(null);
  const [signError, setSignError] = useState('');
  const [scrollEnabled, setScrollEnabled] = useState(true);
  const [isBusy, setIsBusy] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [errors, setErrors] = useState<FieldErrors>({});
  const [isOffline, setIsOffline] = useState(false);
  const [helpers, setHelpers] = useState<ReportHelper[]>([]);
  // Helper assignment id -> hours typed; empty = same as the report's.
  const [helperHours, setHelperHours] = useState<Record<string, string>>({});
  const [notSigned, setNotSigned] = useState(false);
  const [waivedReason, setWaivedReason] = useState('');
  const [sendCopyTo, setSendCopyTo] = useState('');
  const [machines, setMachines] = useState<MachineRow[]>([]);
  const [siteMachines, setSiteMachines] = useState<SiteMachine[]>([]);
  const [brands, setBrands] = useState<Brand[]>([]);
  const [outcomes, setOutcomes] = useState<Choice[]>([]);
  // The report's standard wording to pick from, and what's picked.
  const [findingChoices, setFindingChoices] = useState<Choice[]>([]);
  const [actionChoices, setActionChoices] = useState<Choice[]>([]);
  const [workLineChoices, setWorkLineChoices] = useState<Choice[]>([]);
  const [recommendationChoices, setRecommendationChoices] = useState<Choice[]>([]);
  const [workLines, setWorkLines] = useState<string[]>([]);
  const [recommendations, setRecommendations] = useState<string[]>([]);
  const [recommendation, setRecommendation] = useState('');
  // The customer's report language: anything typed should be in it.
  const [reportLanguage, setReportLanguage] = useState('en');
  // The review before sending (see openReview); null while filling in.
  const [review, setReview] = useState<{ html: string; problems: string[]; offline?: boolean } | null>(null);
  const [pickingMachine, setPickingMachine] = useState(false);

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
      const reportHelpers = reportResult.data.helpers ?? [];
      setHelpers(reportHelpers);
      setHelperHours(Object.fromEntries(reportHelpers.map((helper) => [String(helper.id), helper.labour_hours ?? ''])));
      setSendCopyTo(reportResult.data.send_copy_to ?? '');
      setLabourHours(reportResult.data.time_on_site ?? '');
      setTimeIsFinal(Boolean(reportResult.data.time_is_final));
      const reportBrands = reportResult.data.brands ?? [];
      setBrands(reportBrands);
      setSiteMachines(reportResult.data.site_machines ?? []);
      setOutcomes(reportResult.data.outcomes ?? []);
      setFindingChoices(reportResult.data.findings ?? []);
      setActionChoices(reportResult.data.actions ?? []);
      setWorkLineChoices(reportResult.data.work_line_choices ?? []);
      setRecommendationChoices(reportResult.data.recommendation_choices ?? []);
      setReportLanguage(reportResult.data.report_language ?? 'en');
      // What the report records, or the machines the customer reported.
      setMachines((reportResult.data.machines ?? []).map((machine) => machineRow(machine, reportBrands)));
      const pending = pendingReportFor(Number(id));
      if (pending) {
        // A report already filled in here but not sent yet — carry on from it.
        const input = pending.input;
        setFindings(input.findings);
        setActionTaken(input.action_taken);
        setWorkLines(input.work_lines ?? []);
        setRecommendations(input.recommendations ?? []);
        setRecommendation(input.recommendation ?? '');
        setResolved(input.resolved);
        setCustomerName(input.customer_name);
        setNewSignature(input.signature);
        setSignatureOnFile(Boolean(report?.signature_url));
        setNotSigned(Boolean(input.signature_waived_reason));
        setWaivedReason(input.signature_waived_reason ?? '');
        if (input.send_copy_to !== undefined) {
          setSendCopyTo(input.send_copy_to);
        }
        if (input.helper_hours) {
          setHelperHours((current) => ({ ...current, ...input.helper_hours }));
        }
        if (input.machines) {
          const names = new Map((reportResult.data.site_machines ?? []).map((machine) => [machine.id, machine.name]));
          setMachines(input.machines.map((machine) => machineRow(
            { ...machine, name: machine.asset_id ? names.get(machine.asset_id) : undefined }, reportBrands,
          )));
        }
        setParts(input.parts.map((part) => ({
          part_code: part.part_code, description: part.description, quantity: String(part.quantity),
        })));
        if (pending.error) {
          setErrors({ form: `This report could not be sent: ${pending.error} Please correct it and submit again.` });
        }
      } else if (report) {
        setFindings(report.findings);
        setActionTaken(report.action_taken);
        setWorkLines(report.work_lines ?? []);
        setRecommendations(report.recommendations ?? []);
        setRecommendation(report.recommendation ?? '');
        setResolved(report.resolved);
        setCustomerName(report.customer_name);
        setSignatureOnFile(Boolean(report.signature_url));
        setNotSigned(Boolean(report.signature_waived_reason));
        setWaivedReason(report.signature_waived_reason ?? '');
        setParts(report.parts_used.map((part) => ({
          part_code: part.part_code, description: part.description, quantity: String(part.quantity),
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

  // A pick fills in what follows from it, unless already chosen: "No
  // fault found" means no action and "Inspected OK"; a replacement means
  // "Replaced"; a repair action means "Repaired".
  const withFinding = (row: MachineRow, code: string): Partial<MachineRow> => (
    code === 'no_fault'
      ? { finding_code: code, action_code: row.action_code || 'none_needed', outcome: row.outcome || 'inspected_ok' }
      : { finding_code: code }
  );
  const withAction = (row: MachineRow, code: string): Partial<MachineRow> => {
    const replaced = ['part_replaced', 'cable_replaced', 'belt_replaced', 'console_replaced'];
    const repaired = ['adjusted', 'greased', 'tightened', 'belt_adjusted', 'calibrated'];
    const outcome = replaced.includes(code) ? 'replaced' : repaired.includes(code) ? 'repaired' : '';
    return { action_code: code, ...(outcome && !row.outcome ? { outcome } : {}) };
  };

  const updateMachine = (key: string, changes: Partial<MachineRow>) => {
    setMachines((rows) => rows.map((row) => (row.key === key ? { ...row, ...changes } : row)));
  };

  // Up to five brands matching what's typed, hidden once one is picked.
  const brandSuggestions = (row: MachineRow): Brand[] => {
    const query = row.brand_name.trim().toLowerCase();
    if (!query || row.brand_id) {
      return [];
    }
    return brands.filter((brand) => brand.name.toLowerCase().includes(query)).slice(0, 5);
  };

  const outcomeLabel = (value: string) => outcomes.find((outcome) => outcome.value === value)?.label ?? value;
  const unpickedSiteMachines = siteMachines.filter(
    (machine) => !machines.some((row) => row.asset_id === machine.id),
  );

  // Everything the report holds, as sent to the server.
  const reportInput = () => ({
      findings,
      action_taken: actionTaken,
      work_lines: workLines,
      recommendations,
      recommendation,
      resolved,
      customer_name: customerName,
      // Fully blank rows are just unused space, same as the web form.
      parts: parts
        .filter((row) => row.part_code || row.quantity)
        .map((row) => ({ ...row, quantity: Number(row.quantity) || 0 })),
      signature: notSigned ? null : newSignature,
      signature_waived_reason: notSigned ? waivedReason : '',
      helper_hours: helperHours,
      send_copy_to: sendCopyTo.trim(),
      machines: machines.map(machineInput),
  });

  // "Review report": the report exactly as the customer will get it, and
  // what to fix — nothing is sent until Submit on the review.
  const openReview = async () => {
    setIsBusy(true);
    setErrors({});
    try {
      setReview(await previewMyReport(Number(id), reportInput()));
    } catch (err) {
      if (isNetworkError(err)) {
        setReview({ html: '', problems: [], offline: true });
      } else if (err instanceof ApiRequestError && err.status === 400) {
        const fieldErrors = fieldErrorsFrom(err.body);
        setErrors(Object.keys(fieldErrors).length ? fieldErrors : { form: err.message });
      } else {
        setErrors({ form: err instanceof ApiRequestError ? err.message : 'Unable to prepare the review. Please try again.' });
      }
    } finally {
      setIsBusy(false);
    }
  };

  const submit = async () => {
    setIsBusy(true);
    setErrors({});
    const location = await currentLocation();
    const input = { ...reportInput(), ...(location ?? {}), filed_at: new Date().toISOString() };
    try {
      const result = await submitMyReport(Number(id), input);
      removePendingReport(Number(id));
      Alert.alert('Report saved', result.message);
      router.back();
    } catch (err) {
      if (isNetworkError(err) && task) {
        savePendingReport(Number(id), task.task_number, input);
        Alert.alert(
          'Saved on this device',
          'No connection available. The report will be sent automatically when you are back online.',
        );
        router.back();
      } else if (err instanceof ApiRequestError && err.status === 400) {
        const fieldErrors = fieldErrorsFrom(err.body);
        setErrors(Object.keys(fieldErrors).length ? fieldErrors : { form: err.message });
        setReview(null);  // back to the form, where the errors show
      } else {
        setErrors({ form: err instanceof ApiRequestError ? err.message : 'Unable to save. Please try again.' });
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
              <ThemedText type="smallBold">Findings</ThemedText>
              <ThemedText>{findings || '—'}</ThemedText>
              {actionTaken ? (
                <>
                  <ThemedText type="smallBold">Work performed</ThemedText>
                  <ThemedText>{actionTaken}</ThemedText>
                </>
              ) : null}
              <ThemedText type="smallBold">Problem resolved</ThemedText>
              <ThemedText>{resolved ? 'Yes' : 'No'}</ThemedText>
              {labourHours ? (
                <>
                  <ThemedText type="smallBold">Time on site</ThemedText>
                  <ThemedText>{labourHours} hours</ThemedText>
                </>
              ) : null}
              {machines.some((row) => row.outcome) ? (
                <>
                  <ThemedText type="smallBold">Machines</ThemedText>
                  {machines.filter((row) => row.outcome).map((row) => (
                    <ThemedText key={row.key}>
                      {row.name || `${row.brand_name} ${row.model_name ?? ''}`.trim()}: {outcomeLabel(row.outcome)}
                      {row.finding_code ? ` — ${findingChoices.find((c) => c.value === row.finding_code)?.label ?? ''}` : ''}
                      {row.action_code ? ` → ${actionChoices.find((c) => c.value === row.action_code)?.label ?? ''}` : ''}
                      {row.note ? ` — ${row.note}` : ''}
                    </ThemedText>
                  ))}
                </>
              ) : null}
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
              <ThemedText style={[styles.buttonText, { color: theme.onPrimary }]}>Completed — return to the technician</ThemedText>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => setSigning(false)} style={styles.centerLink}>
              <ThemedText themeColor="textSecondary">Cancel</ThemedText>
            </TouchableOpacity>
          </ScrollView>
        </SafeAreaView>
      </ThemedView>
    );
  }

  if (review) {
    const signed = Boolean(newSignature || signatureOnFile || notSigned);
    return (
      <ThemedView style={styles.container}>
        <SafeAreaView style={styles.safeArea} edges={['top']}>
          <TouchableOpacity onPress={() => setReview(null)} style={styles.backLink}>
            <ThemedText themeColor="primary">← Edit the report</ThemedText>
          </TouchableOpacity>
          <ThemedView style={styles.reviewHead}>
            <ThemedText type="subtitle">Review before sending</ThemedText>
            {review.offline ? (
              <ThemedText themeColor="danger" type="small">
                No connection, so the report can’t be shown. You can still submit: it’s sent when you’re back online.
              </ThemedText>
            ) : review.problems.length ? (
              <>
                <ThemedText themeColor="danger" type="smallBold">Please check {review.problems.length === 1 ? 'this' : 'these'}:</ThemedText>
                {review.problems.map((problem, index) => (
                  <ThemedText key={index} themeColor="danger" type="small">• {problem}</ThemedText>
                ))}
              </>
            ) : (
              <ThemedText type="small" themeColor="textSecondary">Nothing missing. This is the report as the customer will get it.</ThemedText>
            )}
          </ThemedView>
          {review.html ? (
            <WebView
              originWhitelist={['*']}
              source={{ html: review.html, baseUrl: API_BASE_URL }}
              style={styles.reviewPage}
            />
          ) : <ThemedView style={styles.reviewPage} />}
          <ThemedView style={styles.reviewActions}>
            {errors.form ? <ThemedText themeColor="danger" style={styles.formError}>{errors.form}</ThemedText> : null}
            {!signed ? (
              <TouchableOpacity
                style={[styles.secondaryButton, { borderColor: theme.primary }]}
                onPress={() => {
                  setSignError('');
                  setSigning(true);
                }}
              >
                <ThemedText themeColor="primary">Hand to customer for signature</ThemedText>
              </TouchableOpacity>
            ) : null}
            <TouchableOpacity
              style={[styles.button, { backgroundColor: theme.primary }, isBusy && styles.buttonDisabled]}
              onPress={submit}
              disabled={isBusy}
            >
              {isBusy ? <ActivityIndicator color={theme.onPrimary} /> : (
                <ThemedText style={[styles.buttonText, { color: theme.onPrimary }]}>
                  {review.problems.length ? 'Submit anyway' : 'Submit report'}
                </ThemedText>
              )}
            </TouchableOpacity>
          </ThemedView>
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

            <ThemedView style={[styles.languageNote, { backgroundColor: theme.backgroundElement }]}>
              <ThemedText type="small">
                {reportLanguage === 'ar'
                  ? 'This customer’s report is in ARABIC. Pick from the lists where you can; write anything you type in Arabic.'
                  : 'This customer’s report is in English. Pick from the lists where you can; write anything you type in English.'}
              </ThemedText>
            </ThemedView>

            <StepTitle
              number={1} title="Machines"
              hint="For each machine: pick what you found, what you did, and its status. Add any machine that isn't listed."
            />
            <ThemedView style={styles.section}>
              {machines.map((row) => (
                <ThemedView key={row.key} style={[styles.partCard, { borderColor: theme.backgroundSelected }]}>
                  {row.asset_id ? (
                    <ThemedText type="smallBold">
                      {row.name}{row.serial_no ? ` · ${row.serial_no}` : ''}
                    </ThemedText>
                  ) : (
                    <>
                      <TextInput
                        style={inputStyle} value={row.brand_name} placeholder="Brand"
                        placeholderTextColor={theme.textSecondary}
                        onChangeText={(value) => updateMachine(row.key, { brand_name: value, brand_id: null })}
                      />
                      {brandSuggestions(row).map((brand) => (
                        <TouchableOpacity
                          key={brand.id}
                          style={[styles.suggestion, { borderColor: theme.backgroundSelected }]}
                          onPress={() => updateMachine(row.key, { brand_id: brand.id, brand_name: brand.name })}
                        >
                          <ThemedText type="small">{brand.name}</ThemedText>
                        </TouchableOpacity>
                      ))}
                      <TextInput
                        style={inputStyle} value={row.model_name ?? ''} placeholder="Model"
                        placeholderTextColor={theme.textSecondary}
                        onChangeText={(value) => updateMachine(row.key, { model_name: value })}
                      />
                      <TextInput
                        style={[inputStyle, !row.serial_no.trim() && !row.no_serial && { borderColor: theme.danger }]}
                        value={row.serial_no} placeholder="Serial number (from the plate)"
                        placeholderTextColor={theme.textSecondary} autoCapitalize="characters"
                        onChangeText={(value) => updateMachine(row.key, { serial_no: value })}
                      />
                      {row.serial_photo_url ? (
                        <TouchableOpacity onPress={() => Linking.openURL(row.serial_photo_url!)}>
                          <ThemedText type="small" themeColor="primary">See the customer&apos;s photo of the plate ›</ThemedText>
                        </TouchableOpacity>
                      ) : null}
                      <ThemedView style={styles.serialRow}>
                        <Switch
                          value={Boolean(row.no_serial)}
                          onValueChange={(value) => updateMachine(row.key, { no_serial: value })}
                        />
                        <ThemedText type="small" style={styles.serialLabel}>No readable serial plate</ThemedText>
                      </ThemedView>
                      {!row.serial_no.trim() && !row.no_serial ? (
                        <ThemedText type="small" themeColor="danger">
                          The serial number is how this machine is tracked. Type it from the plate, or tick that it has none.
                        </ThemedText>
                      ) : null}
                    </>
                  )}
                  <ThemedView style={styles.chipRow}>
                    {outcomes.map((outcome) => (
                      <TouchableOpacity
                        key={outcome.value}
                        style={[
                          styles.chip, { borderColor: theme.backgroundSelected },
                          row.outcome === outcome.value && { backgroundColor: theme.primary, borderColor: theme.primary },
                        ]}
                        onPress={() => updateMachine(row.key, { outcome: outcome.value })}
                      >
                        <ThemedText
                          type="small"
                          style={row.outcome === outcome.value ? { color: theme.onPrimary } : undefined}
                        >
                          {outcome.label}
                        </ThemedText>
                      </TouchableOpacity>
                    ))}
                  </ThemedView>
                  <ThemedText type="smallBold">Finding</ThemedText>
                  <Chips
                    choices={findingChoices} selected={row.finding_code ? [row.finding_code] : []}
                    onChange={(picked) => updateMachine(row.key, withFinding(row, picked[0] ?? ''))}
                  />
                  <TextInput
                    style={inputStyle} value={row.note} placeholder="Other finding (if none fits)"
                    placeholderTextColor={theme.textSecondary} maxLength={255}
                    onChangeText={(value) => updateMachine(row.key, { note: value })}
                  />
                  <ThemedText type="smallBold">Action taken</ThemedText>
                  <Chips
                    choices={actionChoices} selected={row.action_code ? [row.action_code] : []}
                    onChange={(picked) => updateMachine(row.key, withAction(row, picked[0] ?? ''))}
                  />
                  <TextInput
                    style={inputStyle} value={row.action ?? ''} placeholder="Other action (if none fits)"
                    placeholderTextColor={theme.textSecondary} maxLength={255}
                    onChangeText={(value) => updateMachine(row.key, { action: value })}
                  />
                  <ThemedText type="smallBold">Status</ThemedText>
                  <TouchableOpacity onPress={() => setMachines((rows) => rows.filter((other) => other.key !== row.key))}>
                    <ThemedText themeColor="danger" type="small">Remove machine</ThemedText>
                  </TouchableOpacity>
                </ThemedView>
              ))}
              {errors.machines ? <ThemedText themeColor="danger" type="small">{errors.machines}</ThemedText> : null}
              {pickingMachine ? (
                <>
                  {unpickedSiteMachines.map((machine) => (
                    <TouchableOpacity
                      key={machine.id}
                      style={[styles.suggestion, { borderColor: theme.backgroundSelected }]}
                      onPress={() => {
                        setMachines((rows) => [...rows, machineRow(
                          { asset_id: machine.id, name: machine.name, serial_no: machine.serial_no, outcome: '', note: '' },
                          brands,
                        )]);
                        setPickingMachine(false);
                      }}
                    >
                      <ThemedText type="small">
                        <ThemedText type="smallBold">{machine.name}</ThemedText>
                        {machine.serial_no ? ` · ${machine.serial_no}` : ''}
                      </ThemedText>
                    </TouchableOpacity>
                  ))}
                  {unpickedSiteMachines.length === 0 ? (
                    <ThemedText themeColor="textSecondary" type="small">Every machine at this site is already listed.</ThemedText>
                  ) : null}
                </>
              ) : null}
              {siteMachines.length > 0 ? (
                <TouchableOpacity
                  style={[styles.secondaryButton, { borderColor: theme.primary }]}
                  onPress={() => setPickingMachine((value) => !value)}
                >
                  <ThemedText themeColor="primary">{pickingMachine ? 'Close the list' : '+ Add a machine at this site'}</ThemedText>
                </TouchableOpacity>
              ) : null}
              <TouchableOpacity
                style={[styles.secondaryButton, { borderColor: theme.primary }]}
                onPress={() => setMachines((rows) => [...rows, machineRow(
                  { brand_id: null, model_name: '', serial_no: '', outcome: '', note: '' }, brands,
                )])}
              >
                <ThemedText themeColor="primary">+ Add a machine not in the list</ThemedText>
              </TouchableOpacity>
            </ThemedView>

            <StepTitle number={2} title="Work performed" hint="Tick everything you did on this visit." />
            <Field label="Work performed" error={errors.work_lines}>
              <Chips
                choices={workLineChoices} selected={workLines} multi
                onChange={setWorkLines}
              />
              <TextInput
                style={[inputStyle, styles.multiline]} value={actionTaken} onChangeText={setActionTaken} multiline
                placeholder="Other work, one per line" placeholderTextColor={theme.textSecondary}
              />
            </Field>

            <StepTitle
              number={3} title="Result and advice"
              hint="Say whether the problem is solved, and add advice for the customer if needed."
            />
            <Field label="Has the issue been resolved?" error={errors.resolved}>
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
                    <ThemedText style={resolved === value ? [styles.toggleTextActive, { color: theme.onPrimary }] : undefined}>
                      {value ? 'Yes' : 'No'}
                    </ThemedText>
                  </TouchableOpacity>
                ))}
              </ThemedView>
            </Field>

            <Field label="Overall findings (optional)" error={errors.findings}>
              <TextInput style={[inputStyle, styles.multiline]} value={findings} onChangeText={setFindings} multiline />
            </Field>

            <Field label="Recommendation" error={errors.recommendations}>
              <Chips
                choices={recommendationChoices} selected={recommendations} multi
                onChange={setRecommendations}
              />
              <TextInput
                style={[inputStyle, styles.multiline]} value={recommendation} onChangeText={setRecommendation} multiline
                placeholder="Other recommendation" placeholderTextColor={theme.textSecondary}
              />
            </Field>


            <StepTitle
              number={4} title="Hours and parts"
              hint="Your time on site is worked out from your taps. Add any parts you fitted."
            />
            <ThemedView style={styles.section}>
              <ThemedText type="smallBold">
                Time on site: {labourHours ? `${labourHours} hours` : '—'}
              </ThemedText>
              <ThemedText themeColor="textSecondary" type="small">
                {timeIsFinal
                  ? 'From when you tapped Start until the report was sent, without pauses. Waiting for review doesn’t count.'
                  : 'From when you tapped Start until now, without pauses. It stops when you send the report; waiting for review doesn’t count.'}
              </ThemedText>
            </ThemedView>

            {helpers.length > 0 ? (
              <ThemedView style={styles.section}>
                <ThemedText type="smallBold">Helpers’ hours</ThemedText>
                <ThemedText themeColor="textSecondary" type="small">
                  Each helper gets the time on site. Fill one in only if it was less (someone who left early).
                </ThemedText>
                {helpers.map((helper) => (
                  <Field
                    key={helper.id} label={helper.technician_name} error={errors[`helper_hours_${helper.id}`]}
                  >
                    <TextInput
                      style={inputStyle} value={helperHours[String(helper.id)] ?? ''}
                      onChangeText={(value) => setHelperHours((current) => ({ ...current, [String(helper.id)]: value }))}
                      keyboardType="decimal-pad" placeholder="Same as the report"
                      placeholderTextColor={theme.textSecondary}
                    />
                  </Field>
                ))}
              </ThemedView>
            ) : null}

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
                  </ThemedView>
                  <TouchableOpacity onPress={() => setParts((rows) => rows.filter((_, i) => i !== index))}>
                    <ThemedText themeColor="danger" type="small">Remove part</ThemedText>
                  </TouchableOpacity>
                </ThemedView>
              ))}
              {errors.parts ? <ThemedText themeColor="danger" type="small">{errors.parts}</ThemedText> : null}
              <TouchableOpacity
                style={[styles.secondaryButton, { borderColor: theme.primary }]}
                onPress={() => setParts((rows) => [...rows, emptyPart()])}
              >
                <ThemedText themeColor="primary">+ Add a part</ThemedText>
              </TouchableOpacity>
            </ThemedView>

            <StepTitle
              number={5} title="Customer sign-off"
              hint="Hand the phone to the customer to sign — they see the work and the time on site."
            />
            <ThemedView style={styles.section}>
              <ThemedText type="smallBold">
                Customer sign-off{task.requires_signature ? ' (required)' : ''}
              </ThemedText>
              <ThemedText themeColor="textSecondary" type="small">
                {newSignature
                  ? `Signed by ${customerName || 'the customer'} ✓`
                  : signatureOnFile
                    ? `Signed by ${customerName || 'the customer'} — sign again only if you wish to replace it.`
                    : 'The customer reviews the work and the time on site, and signs on their own screen.'}
              </ThemedText>
              <TouchableOpacity
                style={[styles.secondaryButton, { borderColor: theme.primary }]}
                onPress={() => {
                  setSignError('');
                  setSigning(true);
                }}
              >
                <ThemedText themeColor="primary">
                  {newSignature || signatureOnFile ? 'Sign again' : 'Hand to customer for signature'}
                </ThemedText>
              </TouchableOpacity>
              {errors.customer_name ? (
                <ThemedText themeColor="danger" type="small">{errors.customer_name}</ThemedText>
              ) : null}
              {errors.signature ? <ThemedText themeColor="danger" type="small">{errors.signature}</ThemedText> : null}
              <TouchableOpacity
                style={styles.checkRow}
                onPress={() => setNotSigned((value) => !value)}
                accessibilityRole="checkbox"
                accessibilityState={{ checked: notSigned }}
              >
                <ThemedView
                  style={[
                    styles.checkBox, { borderColor: theme.text },
                    notSigned && { backgroundColor: theme.primary, borderColor: theme.primary },
                  ]}
                >
                  {notSigned ? <ThemedText style={{ color: theme.onPrimary }}>✓</ThemedText> : null}
                </ThemedView>
                <ThemedText>Customer not available to sign</ThemedText>
              </TouchableOpacity>
              {notSigned ? (
                <Field label="Reason the customer could not sign" error={errors.signature_waived_reason}>
                  <TextInput
                    style={[inputStyle, styles.multiline]} value={waivedReason} onChangeText={setWaivedReason}
                    multiline
                  />
                </Field>
              ) : null}
              <Field label="Send a copy to" error={errors.send_copy_to}>
                <TextInput
                  style={inputStyle} value={sendCopyTo} onChangeText={setSendCopyTo}
                  keyboardType="email-address" autoCapitalize="none" autoCorrect={false}
                />
              </Field>
              <ThemedText themeColor="textSecondary" type="small">
                The customer receives this report as a PDF by email. Leave blank to send nothing.
              </ThemedText>
            </ThemedView>

            {errors.form ? <ThemedText themeColor="danger" style={styles.formError}>{errors.form}</ThemedText> : null}

            <StepTitle number={6} title="Review and send" hint="See the report exactly as the customer will get it, then send it." />
            <TouchableOpacity
              style={[styles.button, { backgroundColor: theme.primary }, isBusy && styles.buttonDisabled]}
              onPress={openReview}
              disabled={isBusy}
            >
              {isBusy ? <ActivityIndicator color={theme.onPrimary} /> : (
                <ThemedText style={[styles.buttonText, { color: theme.onPrimary }]}>Review report</ThemedText>
              )}
            </TouchableOpacity>
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </ThemedView>
  );
}

/** A numbered step with its one-line instruction. */
function StepTitle({ number, title, hint }: { number: number; title: string; hint: string }) {
  const theme = useTheme();
  return (
    <ThemedView style={styles.step}>
      <ThemedView style={[styles.stepNumber, { backgroundColor: theme.primary }]}>
        <ThemedText type="smallBold" style={{ color: theme.onPrimary }}>{number}</ThemedText>
      </ThemedView>
      <ThemedView style={styles.stepText}>
        <ThemedText type="subtitle" style={styles.stepTitle}>{title}</ThemedText>
        <ThemedText type="small" themeColor="textSecondary">{hint}</ThemedText>
      </ThemedView>
    </ThemedView>
  );
}

/**
 * Choices as chips: one (tap again to clear) or several (`multi`).
 */
function Chips({ choices, selected, onChange, multi = false }: {
  choices: Choice[]; selected: string[]; onChange: (picked: string[]) => void; multi?: boolean;
}) {
  const theme = useTheme();
  const toggle = (value: string) => {
    if (selected.includes(value)) {
      onChange(selected.filter((other) => other !== value));
    } else {
      onChange(multi ? [...selected, value] : [value]);
    }
  };
  return (
    <ThemedView style={styles.chipRow}>
      {choices.map((choice) => {
        const on = selected.includes(choice.value);
        return (
          <TouchableOpacity
            key={choice.value}
            style={[styles.chip, { borderColor: theme.backgroundSelected }, on && { backgroundColor: theme.primary, borderColor: theme.primary }]}
            onPress={() => toggle(choice.value)}
          >
            <ThemedText type="small" style={on ? { color: theme.onPrimary } : undefined}>{choice.label}</ThemedText>
          </TouchableOpacity>
        );
      })}
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
  serialRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two, backgroundColor: 'transparent' },
  serialLabel: { flex: 1 },
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
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  languageNote: { borderRadius: Spacing.two, padding: Spacing.three },
  step: { flexDirection: 'row', gap: Spacing.two, alignItems: 'flex-start', marginTop: Spacing.two, backgroundColor: 'transparent' },
  stepNumber: { width: 28, height: 28, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  stepText: { flex: 1, backgroundColor: 'transparent' },
  stepTitle: { fontSize: 17 },
  reviewHead: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.two, gap: Spacing.one },
  reviewPage: { flex: 1 },
  reviewActions: { padding: Spacing.three, gap: Spacing.two },
  chip: { borderWidth: 1, borderRadius: Spacing.four, paddingHorizontal: Spacing.three, paddingVertical: Spacing.one },
  partNumbers: { flexDirection: 'row', gap: Spacing.two, backgroundColor: 'transparent' },
  partNumber: { flex: 1 },
  secondaryButton: { borderWidth: 1, borderRadius: Spacing.two, paddingVertical: Spacing.two, alignItems: 'center' },
  summary: { borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.one },
  centerLink: { alignItems: 'center', paddingVertical: Spacing.two },
  signatureBox: { height: 200, borderWidth: 1, borderRadius: Spacing.two, overflow: 'hidden' },
  formError: { textAlign: 'center' },
  checkRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two, minHeight: 44 },
  checkBox: {
    width: 24, height: 24, borderWidth: 2, borderRadius: 4, alignItems: 'center', justifyContent: 'center',
  },
  button: { borderRadius: Spacing.two, paddingVertical: Spacing.three, alignItems: 'center' },
  buttonDisabled: { opacity: 0.6 },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '600' },
});
