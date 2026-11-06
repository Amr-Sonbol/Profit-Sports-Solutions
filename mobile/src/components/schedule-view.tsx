import { useCallback, useState } from 'react';
import { useFocusEffect, useRouter } from 'expo-router';
import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, TouchableOpacity } from 'react-native';

import { ApiRequestError } from '@/api/client';
import { fetchMySchedule } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { withOfflineCopy } from '@/offline';
import type { MySchedule, ScheduleDay, ScheduleItem } from '@/types';

export type ScheduleMode = 'week' | 'month' | 'year';

const ROLE_LABELS: Record<string, string> = { lead: 'Lead', helper: 'Helper', responsible: 'Responsible' };
// Same marks as the website's calendar for urgent work.
const PRIORITY_COLORS: Record<string, string> = { emergency: '#C0392B', high: '#B5760A' };

function day(date: string, options: Intl.DateTimeFormatOptions) {
  return new Date(`${date}T00:00:00`).toLocaleDateString([], options);
}

function time(value: string) {
  return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/** "09:00–11:00", "09:00", "Any time" (a day only) or "" (no day). */
function window(task: ScheduleItem) {
  if (task.scheduled_for) {
    return task.estimated_finish ? `${time(task.scheduled_for)}–${time(task.estimated_finish)}` : time(task.scheduled_for);
  }
  return task.scheduled_date ? 'Any time' : '';
}

/**
 * The website's My schedule calendar on the phone — Week, Month or Year of
 * one person's tasks (their own, or `technicianId` for someone on the
 * viewer's team): overdue first, then the calendar, then what has no day.
 * Weeks start on the country's first working day; days off are marked.
 */
export function ScheduleView({
  mode, onModeChange, technicianId, taskRoute, onToday,
}: {
  mode: ScheduleMode;
  onModeChange: (mode: ScheduleMode) => void;
  technicianId?: number;
  taskRoute: (id: number) => string;
  /** My own schedule's "Today" tab, which lives outside this view. */
  onToday?: () => void;
}) {
  const theme = useTheme();
  const router = useRouter();
  // Where each view is: a week's first day, a month, a year (blank = now).
  const [start, setStart] = useState('');
  const [month, setMonth] = useState('');
  const [year, setYear] = useState('');
  const [selectedDay, setSelectedDay] = useState('');
  const [data, setData] = useState<MySchedule | null>(null);
  const [error, setError] = useState('');
  const [isRefreshing, setIsRefreshing] = useState(false);

  const load = useCallback(async () => {
    const params = {
      view: mode, technician: technicianId,
      ...(mode === 'week' ? { start } : mode === 'month' ? { month } : { year }),
    };
    try {
      const key = `schedule-${technicianId ?? 'me'}-${mode}-${start}${month}${year}`;
      const { data: schedule } = await withOfflineCopy(key, () => fetchMySchedule(params));
      setData(schedule);
      setError('');
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Could not load the schedule.');
    }
  }, [mode, technicianId, start, month, year]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const onRefresh = async () => {
    setIsRefreshing(true);
    await load();
    setIsRefreshing(false);
  };

  const openWeek = (weekStart: string) => {
    setStart(weekStart);
    onModeChange('week');
  };

  const taskRow = (task: ScheduleItem, prefix?: string) => (
    <TouchableOpacity
      key={`${prefix ?? ''}${task.id}`}
      style={[styles.taskRow, { borderColor: theme.backgroundSelected }]}
      onPress={() => router.push(taskRoute(task.id) as never)}
    >
      <ThemedView style={styles.taskTop}>
        <ThemedText type="smallBold" style={styles.flex}>
          {prefix ? `${prefix} · ` : ''}{window(task) ? `${window(task)} · ` : ''}{task.task_number}
        </ThemedText>
        {PRIORITY_COLORS[task.priority] ? (
          <ThemedView style={[styles.dot, { backgroundColor: PRIORITY_COLORS[task.priority] }]} />
        ) : null}
      </ThemedView>
      <ThemedText type="small">{task.customer_name} — {task.site_name}</ThemedText>
      <ThemedText type="small" themeColor="textSecondary">
        {[ROLE_LABELS[task.my_role], task.status_display, task.priority_display].filter(Boolean).join(' · ')}
      </ThemedText>
    </TouchableOpacity>
  );

  const dayHeader = (entry: ScheduleDay) => (
    <ThemedView style={styles.dayHeader}>
      <ThemedText type="smallBold">{day(entry.date, { weekday: 'long', day: 'numeric', month: 'short' })}</ThemedText>
      {entry.is_today ? <Badge text="Today" filled /> : null}
      {entry.is_weekend ? <Badge text="Weekend" /> : null}
    </ThemedView>
  );

  // The toolbar: the four views, the range shown, and ‹ this ›.
  const nav = (() => {
    if (!data) {
      return null;
    }
    if (data.view === 'year') {
      return {
        title: String(data.year),
        prev: () => setYear(String((data.year ?? 0) - 1)), now: () => setYear(''),
        next: () => setYear(String((data.year ?? 0) + 1)), nowLabel: 'This year',
      };
    }
    if (data.view === 'month') {
      return {
        title: day(data.month!, { month: 'long', year: 'numeric' }),
        prev: () => setMonth(data.prev_month!.slice(0, 7)), now: () => setMonth(''),
        next: () => setMonth(data.next_month!.slice(0, 7)), nowLabel: 'This month',
      };
    }
    return {
      title: `${day(data.start!, { day: 'numeric', month: 'short' })} – ${day(data.end!, { day: 'numeric', month: 'short', year: 'numeric' })}`,
      prev: () => setStart(data.prev_start!), now: () => setStart(''),
      next: () => setStart(data.next_start!), nowLabel: 'This week',
    };
  })();

  const selected = data?.days?.find((entry) => entry.date === selectedDay);

  return (
    <ScrollView
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={isRefreshing} onRefresh={onRefresh} />}
    >
      <ThemedView style={styles.tabs}>
        {onToday ? <Tab label="Today" active={false} onPress={onToday} /> : null}
        {(['week', 'month', 'year'] as const).map((option) => (
          <Tab key={option} label={option[0]!.toUpperCase() + option.slice(1)} active={mode === option} onPress={() => onModeChange(option)} />
        ))}
      </ThemedView>

      {error ? <ThemedText themeColor="danger">{error}</ThemedText> : null}
      {!data || data.view !== mode ? <ActivityIndicator /> : (
        <>
          {nav ? (
            <ThemedView style={styles.nav}>
              <NavButton label="‹" onPress={nav.prev} />
              <TouchableOpacity onPress={nav.now} style={styles.navTitle}>
                <ThemedText type="smallBold" style={styles.center}>{nav.title}</ThemedText>
                <ThemedText type="small" themeColor="primary" style={styles.center}>{nav.nowLabel}</ThemedText>
              </TouchableOpacity>
              <NavButton label="›" onPress={nav.next} />
            </ThemedView>
          ) : null}

          {data.overdue.length ? (
            <ThemedView style={[styles.section, styles.overdue, { borderColor: theme.danger }]}>
              <ThemedText type="smallBold" themeColor="danger">Overdue</ThemedText>
              <ThemedText type="small" themeColor="textSecondary">Planned for an earlier day and not done yet.</ThemedText>
              {data.overdue.map((task) => taskRow(
                task, day(task.scheduled_for?.slice(0, 10) ?? task.scheduled_date ?? data.today, { day: 'numeric', month: 'short' }),
              ))}
            </ThemedView>
          ) : null}

          {data.view === 'week' && data.days ? data.days.map((entry) => (
            <ThemedView
              key={entry.date}
              style={[
                styles.day, { borderColor: entry.is_today ? theme.text : theme.backgroundSelected },
                entry.is_weekend && { backgroundColor: theme.backgroundElement },
              ]}
            >
              {dayHeader(entry)}
              {entry.tasks.length ? entry.tasks.map((task) => taskRow(task))
                : <ThemedText type="small" themeColor="textSecondary">No tasks</ThemedText>}
            </ThemedView>
          )) : null}

          {data.view === 'month' && data.days ? (
            <>
              <ThemedView style={styles.grid}>
                {data.days.slice(0, 7).map((entry) => (
                  <ThemedText key={entry.date} type="small" themeColor="textSecondary" style={styles.weekday}>
                    {day(entry.date, { weekday: 'short' })}
                  </ThemedText>
                ))}
                {data.days.map((entry) => (
                  <TouchableOpacity
                    key={entry.date}
                    style={[
                      styles.cell, { borderColor: theme.backgroundSelected },
                      entry.is_weekend && { backgroundColor: theme.backgroundElement },
                      !entry.in_month && styles.otherMonth,
                      entry.date === selectedDay && { borderColor: theme.primary, borderWidth: 2 },
                    ]}
                    onPress={() => setSelectedDay(entry.date)}
                  >
                    <ThemedView style={[styles.cellDate, entry.is_today && { backgroundColor: theme.primary }]}>
                      <ThemedText type="small" style={entry.is_today ? { color: theme.onPrimary } : undefined}>
                        {Number(entry.date.slice(8))}
                      </ThemedText>
                    </ThemedView>
                    {entry.tasks.length ? (
                      <ThemedText type="smallBold" style={styles.cellCount}>{entry.tasks.length}</ThemedText>
                    ) : null}
                  </TouchableOpacity>
                ))}
              </ThemedView>
              {selected ? (
                <ThemedView style={styles.section}>
                  {dayHeader(selected)}
                  {selected.tasks.length ? selected.tasks.map((task) => taskRow(task))
                    : <ThemedText type="small" themeColor="textSecondary">No tasks</ThemedText>}
                </ThemedView>
              ) : (
                <ThemedText type="small" themeColor="textSecondary">Tap a day to see its tasks.</ThemedText>
              )}
            </>
          ) : null}

          {data.view === 'year' && data.months ? (
            <>
              <ThemedText type="small" themeColor="textSecondary">
                Each filled day has tasks. Tap a day for its week, or a month for its month.
              </ThemedText>
              <ThemedView style={styles.yearGrid}>
                {data.months.map((entry) => (
                  <ThemedView key={entry.first} style={[styles.mini, { borderColor: theme.backgroundSelected }]}>
                    <TouchableOpacity
                      style={styles.miniHead}
                      onPress={() => {
                        setMonth(entry.first.slice(0, 7));
                        onModeChange('month');
                      }}
                    >
                      <ThemedText type="smallBold">{day(entry.first, { month: 'short' })}</ThemedText>
                      {entry.count ? <ThemedText type="small" themeColor="textSecondary">{entry.count}</ThemedText> : null}
                    </TouchableOpacity>
                    <ThemedView style={styles.miniGrid}>
                      {entry.cells.map((cell, index) => (cell ? (
                        <TouchableOpacity
                          key={cell.date}
                          disabled={!cell.count}
                          onPress={() => openWeek(cell.week_start)}
                          style={[
                            styles.miniDay,
                            cell.is_weekend && { backgroundColor: theme.backgroundElement },
                            cell.count > 0 && { backgroundColor: theme.primary },
                            cell.is_today && { borderColor: theme.danger, borderWidth: 1.5 },
                          ]}
                        >
                          <ThemedText style={[styles.miniText, cell.count > 0 && { color: theme.onPrimary }]}>
                            {Number(cell.date.slice(8))}
                          </ThemedText>
                        </TouchableOpacity>
                      ) : <ThemedView key={`blank-${index}`} style={styles.miniDay} />))}
                    </ThemedView>
                  </ThemedView>
                ))}
              </ThemedView>
            </>
          ) : null}

          <ThemedView style={styles.section}>
            <ThemedText type="smallBold">Not yet scheduled ({data.unscheduled.length})</ThemedText>
            {data.unscheduled.length ? data.unscheduled.map((task) => taskRow(task))
              : <ThemedText type="small" themeColor="textSecondary">All open tasks have a day.</ThemedText>}
          </ThemedView>
        </>
      )}
    </ScrollView>
  );
}

function Tab({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  const theme = useTheme();
  return (
    <TouchableOpacity
      style={[styles.tab, { borderColor: theme.backgroundSelected }, active && { backgroundColor: theme.primary, borderColor: theme.primary }]}
      onPress={onPress}
    >
      <ThemedText type="small" style={active ? { color: theme.onPrimary } : undefined}>{label}</ThemedText>
    </TouchableOpacity>
  );
}

function NavButton({ label, onPress }: { label: string; onPress: () => void }) {
  const theme = useTheme();
  return (
    <TouchableOpacity style={[styles.navButton, { borderColor: theme.backgroundSelected }]} onPress={onPress}>
      <ThemedText type="subtitle">{label}</ThemedText>
    </TouchableOpacity>
  );
}

function Badge({ text, filled }: { text: string; filled?: boolean }) {
  const theme = useTheme();
  return (
    <ThemedView style={[styles.badge, { borderColor: theme.text }, filled && { backgroundColor: theme.text }]}>
      <ThemedText type="small" style={filled ? { color: theme.background } : undefined}>{text}</ThemedText>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  content: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.six, gap: Spacing.three },
  flex: { flex: 1 },
  center: { textAlign: 'center' },
  tabs: { flexDirection: 'row', gap: Spacing.two, backgroundColor: 'transparent' },
  tab: { flex: 1, borderWidth: 1, borderRadius: 999, paddingVertical: Spacing.two, alignItems: 'center' },
  nav: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two, backgroundColor: 'transparent' },
  navTitle: { flex: 1 },
  navButton: { borderWidth: 1, borderRadius: Spacing.two, paddingHorizontal: Spacing.three, paddingVertical: Spacing.one },
  section: { gap: Spacing.two, backgroundColor: 'transparent' },
  overdue: { borderStartWidth: 4, paddingStart: Spacing.two },
  day: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, gap: Spacing.two },
  dayHeader: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two, backgroundColor: 'transparent' },
  badge: { borderWidth: 1, borderRadius: 999, paddingHorizontal: Spacing.two },
  taskRow: { borderBottomWidth: 1, paddingBottom: Spacing.two, gap: 2 },
  taskTop: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two, backgroundColor: 'transparent' },
  dot: { width: 10, height: 10, borderRadius: 5 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', backgroundColor: 'transparent' },
  weekday: { width: `${100 / 7}%`, textAlign: 'center', paddingBottom: Spacing.one },
  cell: { width: `${100 / 7}%`, height: 54, borderWidth: 0.5, padding: 3, justifyContent: 'space-between' },
  otherMonth: { opacity: 0.35 },
  cellDate: { alignSelf: 'flex-start', minWidth: 22, borderRadius: 11, alignItems: 'center', backgroundColor: 'transparent' },
  cellCount: { alignSelf: 'flex-end' },
  yearGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  mini: { width: '48%', flexGrow: 1, borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.one },
  miniHead: { flexDirection: 'row', justifyContent: 'space-between', paddingHorizontal: 2, paddingBottom: 2 },
  miniGrid: { flexDirection: 'row', flexWrap: 'wrap', backgroundColor: 'transparent' },
  miniDay: { width: `${100 / 7}%`, aspectRatio: 1, alignItems: 'center', justifyContent: 'center', borderRadius: 4 },
  miniText: { fontSize: 9 },
});
