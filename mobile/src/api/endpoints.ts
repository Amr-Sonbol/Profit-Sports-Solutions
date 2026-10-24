import { currentLocation } from '@/location';

import { apiRequest } from './client';
import type {
  MySkills,
  Brand, Candidate, CataloguePart, Choice, Me, MyProgress, MySchedule, PasswordInput, ReportHelper, ReportMachine, SiteMachine,
  TaskDetail, TaskListItem, TeamProgressRow, TeamTaskDetail, Ticket, TodayPlan, WorkReport, WorkReportInput,
} from '@/types';

export function login(username: string, password: string) {
  return apiRequest<{ token: string } & Me>('/api/login/', {
    method: 'POST',
    body: { username, password },
  });
}

export function fetchMe() {
  return apiRequest<Me>('/api/me/');
}

/** Signs out every other phone; the fresh token returned keeps this one in. */
export function setMyPassword(input: PasswordInput) {
  return apiRequest<{ token: string } & Me>('/api/me/password/', { method: 'POST', body: input });
}

export function fetchMyTasks() {
  return apiRequest<TaskListItem[]>('/api/my-tasks/');
}

export function fetchMyTaskDetail(id: number) {
  return apiRequest<TaskDetail>(`/api/my-tasks/${id}/`);
}

export async function sendTaskAction(id: number, action: string, note?: string) {
  // Every tap but undo goes with where the phone is (src/location.ts).
  const location = action === 'undo' ? null : await currentLocation();
  return apiRequest<{ status: string }>(`/api/my-tasks/${id}/action/`, {
    method: 'POST',
    body: { action, ...(note ? { note } : {}), ...(location ?? {}) },
  });
}

export function uploadTaskAttachment(id: number, fileUri: string, fileName: string, mimeType: string, purpose: string) {
  const form = new FormData();
  // React Native's FormData accepts this {uri, name, type} shape in place
  // of a real File/Blob — fetch on this platform knows how to read it.
  form.append('file', { uri: fileUri, name: fileName, type: mimeType } as unknown as Blob);
  form.append('purpose', purpose);
  return apiRequest<void>(`/api/my-tasks/${id}/attachments/`, {
    method: 'POST',
    body: form,
    isFormData: true,
  });
}

export function fetchMyReport(id: number) {
  return apiRequest<{
    report: WorkReport | null;
    helpers?: ReportHelper[];
    send_copy_to?: string;
    // The visit's time from the taps as "1:32", and whether the report was sent (then fixed).
    time_on_site?: string | null;
    time_is_final?: boolean;
    machines?: ReportMachine[];
    site_machines?: SiteMachine[];
    brands?: Brand[];
    outcomes?: Choice[];
    findings?: Choice[];
    actions?: Choice[];
    work_line_choices?: Choice[];
    recommendation_choices?: Choice[];
    report_language?: string;
  }>(
    `/api/my-tasks/${id}/report/`,
  );
}

export function submitMyReport(id: number, report: WorkReportInput) {
  // `report` is null when the server forwarded it to the team instead
  // (sent after this person was taken off the task).
  return apiRequest<{ status: string; message: string; report: WorkReport | null }>(`/api/my-tasks/${id}/report/`, {
    method: 'POST',
    body: report,
  });
}

export function fetchParts() {
  return apiRequest<CataloguePart[]>('/api/parts/');
}

/** Open tasks; with `search`, matches across every status (newest 50). */
export function fetchTeamTasks(search = '') {
  return apiRequest<TaskListItem[]>(search ? `/api/tasks/?q=${encodeURIComponent(search)}` : '/api/tasks/');
}

export function fetchTeamTaskDetail(id: number) {
  return apiRequest<TeamTaskDetail>(`/api/tasks/${id}/`);
}

export function fetchAssignmentCandidates(id: number) {
  return apiRequest<Candidate[]>(`/api/tasks/${id}/candidates/`);
}

export type AssignAction =
  | { action: 'set_lead'; technician: number; end_reason?: string }
  | { action: 'add_helper'; technician: number }
  | { action: 'remove_helper'; assignment_id: number; end_reason: string };

export function assignTask(id: number, body: AssignAction) {
  return apiRequest<TeamTaskDetail>(`/api/tasks/${id}/assign/`, { method: 'POST', body });
}

export function approveTaskReport(id: number) {
  return apiRequest<TeamTaskDetail>(`/api/tasks/${id}/approve/`, { method: 'POST', body: {} });
}

/** New tickets; with `search`, matches across every status (newest 50). */
export function fetchNewTickets(search = '') {
  return apiRequest<Ticket[]>(search ? `/api/tickets/?q=${encodeURIComponent(search)}` : '/api/tickets/');
}

export function fetchMyToday() {
  return apiRequest<TodayPlan>('/api/my-today/');
}

/** `month` is YYYY-MM; this month when left out. */
export function fetchMyProgress(month?: string) {
  return apiRequest<MyProgress>(`/api/my-progress/${month ? `?month=${month}` : ''}`);
}

export function fetchTeamProgress(month?: string) {
  return apiRequest<{ month: string; rows: TeamProgressRow[] }>(`/api/team-progress/${month ? `?month=${month}` : ''}`);
}

/**
 * A week (`start` YYYY-MM-DD), month (`month` YYYY-MM) or year of someone's
 * schedule; `technician` for a person on the viewer's team.
 */
export function fetchMySchedule(params: { view: string; start?: string; month?: string; year?: string; technician?: number }) {
  const query = Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== '')
    .map(([key, value]) => `${key}=${encodeURIComponent(String(value))}`)
    .join('&');
  return apiRequest<MySchedule>(`/api/my-schedule/?${query}`);
}

/**
 * "Review report": the report exactly as it would be sent (HTML) and what
 * to fix — nothing is saved. The signature isn't needed yet.
 */
export function previewMyReport(id: number, report: WorkReportInput) {
  return apiRequest<{ html: string; problems: string[] }>(`/api/my-tasks/${id}/report/?preview=1`, {
    method: 'POST',
    body: { ...report, signature: null },
  });
}

/** My skills: progress, what the levels mean, every skill and conduct area. */
export function fetchMySkills() {
  return apiRequest<MySkills>('/api/my-skills/');
}

/**
 * A self-rating, until a supervisor reviews it. A skill needs evidence:
 * a photo or video of doing the repair. Returns My skills again.
 */
export function rateMySkill(
  kind: 'skill' | 'conduct', id: number, level: number, note: string,
  evidence?: { uri: string; name: string; type: string },
) {
  const form = new FormData();
  form.append('kind', kind);
  form.append('id', String(id));
  form.append('level', String(level));
  form.append('note', note);
  if (evidence) {
    form.append('evidence', evidence as unknown as Blob);
  }
  return apiRequest<MySkills>('/api/my-skills/', { method: 'POST', body: form, isFormData: true });
}
