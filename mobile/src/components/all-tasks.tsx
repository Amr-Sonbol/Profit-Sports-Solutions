import DateTimePicker, { type DateTimePickerEvent } from '@react-native-community/datetimepicker';
import { useRouter } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, FlatList, Platform, RefreshControl, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';
import { TextInput } from '@/components/text-field';

import { fetchInternalTickets, fetchTaskFilterChoices, fetchTeamTasksPage } from '@/api/endpoints';
import { ChoicePicker } from '@/components/choice-picker';
import { InternalTaskRow } from '@/components/internal-tasks';
import { TaskCard } from '@/components/task-card';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { InternalTicket, TaskListItem } from '@/types';
import { locale, t } from '@/i18n';

type Option = { value: string; label: string };
const day = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const shortDay = (d: Date) => d.toLocaleDateString(locale(), { day: 'numeric', month: 'short' });
const inDays = (n: number) => { const d = new Date(); d.setDate(d.getDate() + n); return d; };

// After the website's status tabs: the field's own quick looks.
type Extra = '' | 'no_lead' | 'no_day' | 'today' | 'week' | 'dates';
const EXTRAS: [Exclude<Extra, '' | 'dates'>, string][] = [
  ['no_lead', 'No technician'], ['no_day', 'No date'], ['today', 'Today'], ['week', 'This week'],
];
// "More filters": the website task list's own fields (tasks.views.filtered_tasks).
const TEXT_FILTERS: [string, string][] = [
  ['customer_name', 'Customer'], ['site', 'Site (branch or city)'], ['serial_number', 'Machine serial number'],
  ['pak_reference', 'Panatta PAK'],
];

/**
 * Every task in the country, as the website's task list finds them: one
 * search box (as you type), the website's status tabs as chips, the field's
 * quick looks (no technician, no day, today, this week, dates), and "More
 * filters" with the website's own fields. 30 at a time, more as you scroll.
 */
/**
 * `withInternal`: whoever has the Internal tab sees the country's internal
 * tasks here too, at the top — "All" means all — following the search and
 * Open / All; hidden for the filters only field tasks have.
 */
export function AllTasks({ withInternal = false }: { withInternal?: boolean }) {
  const router = useRouter();
  const theme = useTheme();
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('open');
  const [extra, setExtra] = useState<Extra>('');
  const [range, setRange] = useState(() => ({ from: new Date(), to: new Date() }));
  const [pickingDate, setPickingDate] = useState<'from' | 'to' | null>(null);
  const [more, setMore] = useState<Record<string, string>>({});
  const [showMore, setShowMore] = useState(false);
  const [choices, setChoices] = useState<{ statuses: Option[]; technicians: Option[]; shipping_companies: Option[] } | null>(null);
  const [items, setItems] = useState<TaskListItem[] | null>(null);
  const [internal, setInternal] = useState<InternalTicket[]>([]);
  const [count, setCount] = useState(0);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const latest = useRef(0);

  const params = useCallback((): Record<string, string> => {
    const out: Record<string, string> = { status };
    if (query.trim()) out.q = query.trim();
    if (extra === 'no_lead') out.lead = 'none';
    if (extra === 'no_day') out.scheduled = 'none';
    const dates = extra === 'today' ? [new Date(), new Date()] : extra === 'week' ? [new Date(), inDays(6)]
      : extra === 'dates' ? [range.from, range.to] : null;
    if (dates) {
      out.scheduled_from = day(dates[0]);
      out.scheduled_to = day(dates[1]);
    }
    for (const [key, value] of Object.entries(more)) if (value.trim()) out[key] = value.trim();
    return out;
  }, [status, query, extra, range, more]);

  // A new search: page 1. Only the newest answer is shown (typing fires several).
  const search = useCallback(async () => {
    const ticket = ++latest.current;
    setLoading(true);
    try {
      const result = await fetchTeamTasksPage(params(), 1);
      if (ticket !== latest.current) return;
      setItems(result.results);
      setCount(result.count);
      setPage(1);
      setHasMore(result.has_more);
      setError('');
    } catch {
      if (ticket === latest.current) setError('Could not load the tasks.');
    } finally {
      if (ticket === latest.current) setLoading(false);
    }
  }, [params]);

  // The internal tasks: only for Open or All (their statuses are their own), and no field-only filter.
  const internalStatus = status === 'open' || status === 'all' ? status : null;
  const fieldOnly = Boolean(extra) || Object.values(more).some((value) => value.trim());
  const showsInternal = withInternal && Boolean(internalStatus) && !fieldOnly;
  useEffect(() => {
    if (!showsInternal || !internalStatus) {
      return undefined;
    }
    const timer = setTimeout(() => {
      fetchInternalTickets(internalStatus, query.trim()).then(setInternal).catch(() => setInternal([]));
    }, 400);
    return () => clearTimeout(timer);
  }, [showsInternal, internalStatus, query]);

  // Typing waits for a short pause; any other change searches at once.
  const typing = useRef(false);
  useEffect(() => {
    const timer = setTimeout(search, typing.current ? 400 : 0);
    typing.current = false;
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    fetchTaskFilterChoices().then(setChoices).catch(() => undefined);
  }, []);

  const loadMore = async () => {
    if (!hasMore || loading) return;
    setLoading(true);
    try {
      const result = await fetchTeamTasksPage(params(), page + 1);
      setItems((current) => [...(current ?? []), ...result.results]);
      setPage(result.page);
      setHasMore(result.has_more);
    } catch {
      setError('Could not load more tasks.');
    } finally {
      setLoading(false);
    }
  };

  const chip = (label: string, on: boolean, onPress: () => void, key?: string) => (
    <TouchableOpacity
      key={key ?? label} onPress={onPress}
      style={[styles.chip, { borderColor: on ? theme.primary : theme.backgroundSelected }, on && { backgroundColor: theme.primary }]}
    >
      <ThemedText type="small" style={on ? { color: theme.onPrimary } : undefined}>{label}</ThemedText>
    </TouchableOpacity>
  );

  const statusTabs: Option[] = [{ value: 'open', label: 'Open' }, { value: 'all', label: 'All' }, ...(choices?.statuses ?? [])];
  const activeMore = Object.entries(more).filter(([, value]) => value.trim());
  const label = (key: string, value: string) => {
    if (key === 'technician') return `${t('Technician:')} ${choices?.technicians.find((t) => t.value === value)?.label ?? value}`;
    if (key === 'shipping_company') return `${t('Courier:')} ${choices?.shipping_companies.find((c) => c.value === value)?.label ?? value}`;
    return `${TEXT_FILTERS.find(([k]) => k === key)?.[1] ?? key}: ${value}`;
  };

  return (
    <FlatList
      data={items ?? []}
      keyExtractor={(item) => String(item.id)}
      contentContainerStyle={styles.list}
      keyboardShouldPersistTaps="handled"
      refreshControl={<RefreshControl refreshing={false} onRefresh={search} />}
      onEndReached={loadMore}
      onEndReachedThreshold={0.4}
      ListHeaderComponent={(
        <ThemedView style={styles.head}>
          <TextInput
            style={[styles.input, { color: theme.text, borderColor: theme.backgroundSelected }]}
            value={query} onChangeText={(text) => { typing.current = true; setQuery(text); }}
            returnKeyType="search" autoCorrect={false} placeholderTextColor={theme.textSecondary}
            placeholder="Number, customer, branch, serial, technician, request, phone"
          />
          {/* The website's status tabs, in its order. */}
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
            {statusTabs.map((tab) => chip(tab.label, status === tab.value, () => setStatus(tab.value), tab.value))}
          </ScrollView>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
            {EXTRAS.map(([value, text]) => chip(text, extra === value, () => setExtra(extra === value ? '' : value), value))}
            {chip(extra === 'dates' ? `${shortDay(range.from)} – ${shortDay(range.to)}` : 'Dates…', extra === 'dates', () => {
              setExtra('dates');
              setPickingDate('from');
            }, 'dates')}
          </ScrollView>
          {extra === 'dates' ? (
            <ThemedView style={styles.row}>
              {chip(`${t('From:')} ${shortDay(range.from)}`, false, () => setPickingDate('from'), 'from')}
              {chip(`${t('To:')} ${shortDay(range.to)}`, false, () => setPickingDate('to'), 'to')}
            </ThemedView>
          ) : null}
          {pickingDate ? (
            <DateTimePicker
              value={range[pickingDate]} mode="date" display={Platform.OS === 'ios' ? 'inline' : 'default'}
              onChange={(event: DateTimePickerEvent, date?: Date) => {
                const which = pickingDate;
                if (event.type === 'dismissed' || !date) {
                  setPickingDate(null);
                  return;
                }
                const next = { ...range, [which]: date };
                if (next.to < next.from) next.to = next.from;
                setRange(next);
                setPickingDate(which === 'from' ? 'to' : null);
              }}
            />
          ) : null}

          <ThemedView style={styles.row}>
            <TouchableOpacity onPress={() => setShowMore(!showMore)}>
              <ThemedText type="small" themeColor="primary">
                {showMore ? 'Hide filters' : `More filters${activeMore.length ? ` (${activeMore.length})` : ''}`}
              </ThemedText>
            </TouchableOpacity>
            {activeMore.length || extra || status !== 'open' || query ? (
              <TouchableOpacity onPress={() => { setMore({}); setExtra(''); setStatus('open'); setQuery(''); }}>
                <ThemedText type="small" themeColor="danger">Clear all</ThemedText>
              </TouchableOpacity>
            ) : null}
          </ThemedView>
          {showMore ? (
            <ThemedView style={[styles.panel, { borderColor: theme.backgroundSelected }]}>
              <ChoicePicker
                label="Technician" choices={choices?.technicians ?? []} value={more.technician ?? ''} optional
                onChange={(value) => setMore((all) => ({ ...all, technician: value }))}
              />
              {TEXT_FILTERS.map(([key, text]) => (
                <ThemedView key={key} style={styles.field}>
                  <ThemedText type="small" themeColor="textSecondary">{text}</ThemedText>
                  <TextInput
                    style={[styles.input, { color: theme.text, borderColor: theme.backgroundSelected }]}
                    defaultValue={more[key] ?? ''} autoCorrect={false} returnKeyType="search"
                    onEndEditing={(event) => setMore((all) => ({ ...all, [key]: event.nativeEvent.text }))}
                  />
                </ThemedView>
              ))}
              <ChoicePicker
                label="Shipping company" choices={choices?.shipping_companies ?? []} value={more.shipping_company ?? ''} optional
                onChange={(value) => setMore((all) => ({ ...all, shipping_company: value }))}
              />
            </ThemedView>
          ) : null}
          {activeMore.length ? (
            <ThemedView style={styles.row}>
              {activeMore.map(([key, value]) => (
                <TouchableOpacity
                  key={key} onPress={() => setMore((all) => ({ ...all, [key]: '' }))}
                  style={[styles.chip, { borderColor: theme.primary }]} accessibilityLabel={t('Remove {what}', { what: label(key, value) })}
                >
                  <ThemedText type="small" themeColor="primary">{label(key, value)}  ✕</ThemedText>
                </TouchableOpacity>
              ))}
            </ThemedView>
          ) : null}
          {error ? <ThemedText themeColor="danger" type="small">{error}</ThemedText> : null}
          {/* Internal tasks first ("All" means all), each labelled; then the field tasks. */}
          {showsInternal && internal.length ? (
            <ThemedView style={styles.internal}>
              <ThemedText type="smallBold">{t('Internal tasks ({count})', { count: internal.length })}</ThemedText>
              {internal.map((item) => <InternalTaskRow key={item.id} item={item} labelled />)}
            </ThemedView>
          ) : null}
          {items ? <ThemedText type="small" themeColor="textSecondary">{t('{count} field tasks · newest first', { count })}</ThemedText> : null}
        </ThemedView>
      )}
      ListEmptyComponent={items && !loading ? <ThemedText themeColor="textSecondary">No tasks match.</ThemedText> : undefined}
      ListFooterComponent={loading ? <ActivityIndicator style={styles.footer} /> : undefined}
      renderItem={({ item }) => <TaskCard item={item} onPress={() => router.push(`/team-task/${item.id}`)} />}
    />
  );
}

const styles = StyleSheet.create({
  internal: { gap: Spacing.two, backgroundColor: 'transparent' },
  list: { paddingHorizontal: Spacing.three, gap: Spacing.two, paddingBottom: Spacing.six },
  head: { gap: Spacing.two, marginBottom: Spacing.one, backgroundColor: 'transparent' },
  input: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two },
  chips: { gap: Spacing.two },
  chip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: Spacing.three, paddingVertical: Spacing.one },
  row: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', gap: Spacing.two, backgroundColor: 'transparent' },
  panel: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three, gap: Spacing.two },
  field: { gap: 2, backgroundColor: 'transparent' },
  footer: { paddingVertical: Spacing.three },
});
