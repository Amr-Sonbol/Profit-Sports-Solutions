import type { TaskDetail } from '@/types';
import { locale, t } from '@/i18n';

/** "Sat 24 Oct, 10:00–13:00", "Sat 24 Oct, any time", or "Not scheduled yet". */
export function when(task: TaskDetail) {
  if (task.scheduled_for) {
    const start = new Date(task.scheduled_for);
    const day = start.toLocaleDateString(locale(), { weekday: 'short', day: 'numeric', month: 'short' });
    const from = start.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' });
    if (task.estimated_hours) {
      const end = new Date(start.getTime() + Number(task.estimated_hours) * 3600 * 1000);
      return `${day}, ${from}–${end.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' })}`;
    }
    return `${day}, ${from}`;
  }
  if (task.scheduled_date) {
    const day = new Date(`${task.scheduled_date}T00:00:00`);
    return t('{day}, any time', { day: day.toLocaleDateString(locale(), { weekday: 'short', day: 'numeric', month: 'short' }) });
  }
  return 'Not scheduled yet';
}

/** How long ago something arrived: "just now", "3 h ago", "yesterday", "4 days ago"; from a week on, its date ("2 Nov"). */
export function ago(at: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(at).getTime()) / 60000));
  if (minutes < 60) {
    return minutes < 2 ? 'just now' : t('{count} min ago', { count: minutes });
  }
  const hours = Math.round(minutes / 60);
  if (hours < 24) {
    return t('{count} h ago', { count: hours });
  }
  const days = Math.round(hours / 24);
  if (days < 7) {
    return days === 1 ? 'yesterday' : t('{count} days ago', { count: days });
  }
  return new Date(at).toLocaleDateString(locale(), { day: 'numeric', month: 'short' });
}
