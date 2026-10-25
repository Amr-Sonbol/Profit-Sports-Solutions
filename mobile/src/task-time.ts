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
