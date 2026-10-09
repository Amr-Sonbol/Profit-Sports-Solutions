import { currentLocation } from '@/location';

import { apiRequest } from './client';
import type {
  Candidate, CataloguePart, Me, ReportHelper, TaskDetail, TaskListItem, TeamTaskDetail, Ticket, WorkReport, WorkReportInput,
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
  return apiRequest<{ report: WorkReport | null; helpers?: ReportHelper[] }>(`/api/my-tasks/${id}/report/`);
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
