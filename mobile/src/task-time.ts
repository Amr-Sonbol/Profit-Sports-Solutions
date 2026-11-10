import type { TaskDetail } from '@/types';

/** "Sat 24 Oct, 10:00–13:00", "Sat 24 Oct, any time", or "Not scheduled yet". */
export function when(task: TaskDetail) {
  if (task.scheduled_for) {
    const start = new Date(task.scheduled_for);
    const day = start.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' });
    const from = start.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    if (task.estimated_hours) {
      const end = new Date(start.getTime() + Number(task.estimated_hours) * 3600 * 1000);
      return `${day}, ${from}–${end.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
    }
    return `${day}, ${from}`;
  }
  if (task.scheduled_date) {
    const day = new Date(`${task.scheduled_date}T00:00:00`);
    return `${day.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })}, any time`;
  }
  return 'Not scheduled yet';
}

/** How long ago something arrived: "just now", "3 h ago", "yesterday", "4 days ago"; from a week on, its date ("2 Nov"). */
export function ago(at: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(at).getTime()) / 60000));
  if (minutes < 60) {
    return minutes < 2 ? 'just now' : `${minutes} min ago`;
  }
  const hours = Math.round(minutes / 60);
  if (hours < 24) {
    return `${hours} h ago`;
  }
  const days = Math.round(hours / 24);
  if (days < 7) {
    return days === 1 ? 'yesterday' : `${days} days ago`;
  }
  return new Date(at).toLocaleDateString([], { day: 'numeric', month: 'short' });
}
