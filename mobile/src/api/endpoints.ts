import { File, UploadType } from 'expo-file-system';
import { currentLocation } from '@/location';

import { API_BASE_URL, ApiRequestError, apiRequest, getActiveCountry, getToken } from './client';
import type {
  PlannedLine,
  TaskAttachment,
  ChecklistRow,
  StaffAccount,
  Dashboard,
  TeamMap,
  InternalTicket,
  InternalTicketDetail,
  HoursPage,
  CustomerPage, CustomerRow, MachinePage, MachineRow,
  FormFieldSpec,
  WebsiteMenuGroup,
  Person,
  MySkills,
  Brand, Candidate, CataloguePart, Choice, Me, MyProgress, MySchedule, PasswordInput, ReportHelper, ReportMachine, SiteMachine,
  TaskDetail, TaskListItem, TeamProgressRow, TeamTaskDetail, TeamTodayRow, TicketDecision, TicketDetail, TicketPage, TodayPlan,
  WorkReport, WorkReportInput,
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

/** Edit my profile: phone, email, language and a new photo (any left out stay). */
/** A new email also needs `password`; it changes once the link sent there is opened (`notice` says so). */
export function updateMe(changes: { phone?: string; email?: string; password?: string; language?: string },
  photo?: { uri: string; name: string; type: string }) {
  const form = new FormData();
  Object.entries(changes).forEach(([field, value]) => form.append(field, value ?? ''));
  if (photo) {
    form.append('photo', photo as unknown as Blob);
  }
  return apiRequest<Me & { notice?: string }>('/api/me/', { method: 'POST', body: form, isFormData: true });
}

/** "Use this" / "Use these": the customer's photos of a machine for one slot (serial plate or before) —
 * tasks.views.confirm_customer_files. */
export function confirmTaskPhotos(id: number, asset: number, slot: 'serial_plate' | 'before') {
  return apiRequest<TaskAttachment[]>(`/api/my-tasks/${id}/confirm-photo/`, { method: 'POST', body: { asset, slot } });
}

/** A machine registered during the visit, so its photos can be taken now (tasks.views.register_task_machine). */
export function registerTaskMachine(id: number, machine: {
  brand: number; model_name: string; serial_no: string; no_serial: boolean; code: string; ticket_machine?: number | null;
}) {
  return apiRequest<{ asset_id: number; name: string; serial_no: string }>(`/api/my-tasks/${id}/machines/`, {
    method: 'POST', body: machine,
  });
}

/** One part a repair needs (tasks.parts_needed). */
export type PartNeededRow = {
  id: number; code: string; description: string; quantity: number;
  state: 'stock' | 'waiting' | 'arrived'; expected_on: string | null;
};

/** Add a part needed, or (with `line`) mark one arrived / remove one — the task's owner. */
export function changeTaskParts(id: number, body: Record<string, string | number>) {
  return apiRequest<{ parts_needed: PartNeededRow[]; can_change_parts: boolean; can_prepare_parts: boolean }>(
    `/api/tasks/${id}/parts-needed/`, { method: 'POST', body },
  );
}

/** The warehouse (or a manager): the repair's parts are prepared. */
export function markPartsPrepared(id: number) {
  return apiRequest<{ parts_prepared: boolean }>(`/api/tasks/${id}/parts-prepared/`, { method: 'POST', body: {} });
}

/** The repairs whose parts are all in, to prepare — the soonest visit first. */
export type PartsToPrepare = {
  task_id: number; task_number: string; customer: string; site: string; scheduled_for: string | null;
  parts: { code: string; description: string; quantity: number }[];
};
export function fetchPartsToPrepare() {
  return apiRequest<PartsToPrepare[]>('/api/parts-to-prepare/');
}

/** A lost or handed-on phone: every other phone is signed out; the fresh token keeps this one in. */
export function signOutOtherPhones(pushToken: string | null) {
  return apiRequest<{ token: string }>('/api/me/sign-out-others/', {
    method: 'POST', body: { push_token: pushToken ?? '' },
  });
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

// A part shipment handed to the courier: the courier, and its tracking
// number when it has one (local couriers often don't). The server emails
// the customer and settles their request.
export function markShipped(id: number, shippingCompany: string, trackingNumber: string) {
  return apiRequest<{ status: string }>(`/api/my-tasks/${id}/action/`, {
    method: 'POST',
    body: { action: 'shipped', shipping_company: shippingCompany, shipping_tracking_number: trackingNumber },
  });
}

/** `assetId`: the machine the photo shows (its card on the report). Returns the saved photo. */
export async function uploadTaskAttachment(
  id: number, fileUri: string, fileName: string, mimeType: string, purpose: string, assetId?: number,
): Promise<TaskAttachment | null> {
  // Sent by the file system's own multipart upload (expo-file-system): this
  // SDK's fetch won't send a photo picked from the gallery as form data
  // ("Unsupported FormDataPart implementation"). Same sign-in headers as apiRequest.
  const token = await getToken();
  const country = await getActiveCountry();
  const result = await new File(fileUri).upload(`${API_BASE_URL}/api/my-tasks/${id}/attachments/`, {
    httpMethod: 'POST',
    uploadType: UploadType.MULTIPART,
    fieldName: 'file',
    mimeType,
    parameters: { purpose, ...(assetId ? { asset: String(assetId) } : {}) },
    headers: {
      ...(token ? { Authorization: `Token ${token}` } : {}),
      ...(country ? { 'X-Active-Country': country } : {}),
    },
  });
  if (result.status < 200 || result.status >= 300) {
    let body: unknown = null;
    try {
      body = JSON.parse(result.body);
    } catch {
      body = null;
    }
    throw new ApiRequestError(result.status, body);
  }
  try {
    return JSON.parse(result.body) as TaskAttachment;
  } catch {
    return null;
  }
}

/** Sends one file (multipart) with text fields, signed in as for apiRequest. Returns the parsed reply. */
export async function uploadFileTo<T>(path: string, fileUri: string, mimeType: string, fieldName: string, parameters: Record<string, string>): Promise<T> {
  const token = await getToken();
  const country = await getActiveCountry();
  const result = await new File(fileUri).upload(`${API_BASE_URL}${path}`, {
    httpMethod: 'POST',
    uploadType: UploadType.MULTIPART,
    fieldName,
    mimeType,
    parameters,
    headers: {
      ...(token ? { Authorization: `Token ${token}` } : {}),
      ...(country ? { 'X-Active-Country': country } : {}),
    },
  });
  let body: unknown = null;
  try {
    body = JSON.parse(result.body);
  } catch {
    body = null;
  }
  if (result.status < 200 || result.status >= 300) {
    throw new ApiRequestError(result.status, body);
  }
  return body as T;
}

/** One field of the website's Edit task form, as the app draws it (api.views._describe_form). */
export interface EditField {
  name: string;
  label: string;
  kind: 'text' | 'textarea' | 'number' | 'choice' | 'bool' | 'datetime' | 'date' | 'file';
  required: boolean;
  value: string | boolean;
  choices: { value: string; label: string }[];
  help: string;
}

export function fetchTaskEditForm(id: number) {
  return apiRequest<{ fields: EditField[] }>(`/api/tasks/${id}/edit/`);
}

/** Saves the given fields (the rest stay as they are). */
export function saveTaskEdit(id: number, values: Record<string, string | boolean>) {
  return apiRequest<TeamTaskDetail & { message: string; warnings: string[] }>(`/api/tasks/${id}/edit/`, { method: 'POST', body: values });
}

export function fetchMyReport(id: number) {
  return apiRequest<{
    report: WorkReport | null;
    /** Before the first filing: the parts the task needed (code, description, quantity). */
    parts_suggested?: { part_code: string; description: string; quantity: number }[];
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
    // An installation: the machines to check off, and what can be chosen for each.
    checklist?: ChecklistRow[] | null;
    checklist_statuses?: Choice[];
    checklist_step?: 'receiving' | 'loading' | 'installation' | '';
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

/** A page of the country's tasks (30), with the website task list's filters; how many in all, and whether there's more. */
export function fetchTeamTasksPage(filters: Record<string, string>, page: number) {
  const params = new URLSearchParams({ ...filters, page: String(page) }).toString();
  return apiRequest<{ count: number; page: number; has_more: boolean; results: TaskListItem[] }>(`/api/tasks/?${params}`);
}

/** What the task filters offer: the website's status tabs, the country's technicians, the couriers. */
export function fetchTaskFilterChoices() {
  type Option = { value: string; label: string };
  return apiRequest<{ statuses: Option[]; technicians: Option[]; shipping_companies: Option[] }>('/api/tasks/filters/');
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
  | { action: 'remove_helper'; assignment_id: number; end_reason: string }
  /** The visit's day and time, local (YYYY-MM-DDTHH:MM), before anyone has set off. */
  | { action: 'schedule'; scheduled_for: string };

export function assignTask(id: number, body: AssignAction) {
  return apiRequest<TeamTaskDetail>(`/api/tasks/${id}/assign/`, { method: 'POST', body });
}

/** One message on a task's team thread. */
export interface TaskMessage {
  id: number;
  message: string;
  sent_at: string;
  sent_by: string;
  role: string;
  mine: boolean;
}

/** The task's team messages (reading them marks them seen). */
export function fetchTaskMessages(id: number) {
  return apiRequest<TaskMessage[]>(`/api/tasks/${id}/messages/`);
}

/** Adds a message; everyone on the task is told. Returns the thread. */
export function postTaskMessage(id: number, message: string) {
  return apiRequest<TaskMessage[]>(`/api/tasks/${id}/messages/`, { method: 'POST', body: { message } });
}

/** One of the website task page's actions (api.views.TaskActionView). Returns the task again. */
export function taskAction(id: number, body: Record<string, unknown>) {
  return apiRequest<TeamTaskDetail>(`/api/tasks/${id}/action/`, { method: 'POST', body });
}

/** One bell item (api.views.BellView): its words, what it's about, and what a tap opens. */
export interface BellItem {
  kind: string;
  text: string;
  about: string;
  at: string;
  alarm: boolean;
  /** An app screen by name and id, an app path ('app'), or a website page inside the app ('website'). */
  opens: { screen: 'team-task' | 'task' | 'ticket' | 'app' | 'website'; id?: number; path?: string } | null;
}

/** The bell: the newest items and how many there are in all. */
export function fetchBell() {
  return apiRequest<{ count: number; items: BellItem[] }>('/api/bell/');
}

export function approveTaskReport(id: number) {
  return apiRequest<TeamTaskDetail>(`/api/tasks/${id}/approve/`, { method: 'POST', body: {} });
}

/** Something waiting on my decision (tasks.views.waiting_for). */
export interface WaitingItem {
  kind: 'approve_report' | 'review_report' | 'schedule_change' | 'blocked' | 'escalation' | 'internal' | 'late_report';
  title: string;
  text: string;
  detail: string;
  at: string | null;
  opens: { screen: 'team-task' | 'ticket' | 'website'; id?: number; path?: string };
}

/** Waiting for me: the decisions others are waiting on, oldest first. */
export function fetchWaiting() {
  return apiRequest<WaitingItem[]>('/api/waiting/');
}

/** Team → Today: the field team I oversee, right now. */
export function fetchTeamToday() {
  return apiRequest<TeamTodayRow[]>('/api/team-today/');
}

/** A page (30) of customer requests on one tab (new, escalated, converted, closed, dismissed, all), searchable. */
export function fetchTicketsPage(status: string, search: string, page: number) {
  const params = new URLSearchParams({ status, page: String(page), ...(search ? { q: search } : {}) }).toString();
  return apiRequest<TicketPage>(`/api/tickets/?${params}`);
}

/** One customer request, as on the website's request page. */
export function fetchTicket(id: number) {
  return apiRequest<TicketDetail>(`/api/tickets/${id}/`);
}

/** Our reply to the customer (email + their bell), or with `note` a note for staff only. */
export function sendTicketMessage(id: number, message: string, note = false) {
  return apiRequest<{ message: string }>(`/api/tickets/${id}/reply/`, { method: 'POST', body: { message, note } });
}

/** "What happens next?": a visit or a part shipment (a task), or an answer without a visit. */
export function decideTicket(id: number, body: TicketDecision) {
  return apiRequest<{ message: string; task: { id: number; task_number: string } | null }>(
    `/api/tickets/${id}/next/`, { method: 'POST', body },
  );
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
    // The signature goes too: the review shows it (the server never stores it for a review).
    body: report,
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

/** Someone on my team: details, availability, skills and work record. */
export function fetchPerson(id: number) {
  return apiRequest<Person>(`/api/people/${id}/`);
}

/** A supervisor confirming or adjusting one of their levels. */
export function reviewPersonSkill(id: number, kind: 'skill' | 'conduct', itemId: number, level: number, note: string) {
  return apiRequest<Person>(`/api/people/${id}/skills/`, {
    method: 'POST', body: { kind, id: itemId, level, note },
  });
}

/** Available again, or unavailable with a reason. */
export function setPersonAvailability(id: number, available: boolean, reason = '') {
  return apiRequest<Person>(`/api/people/${id}/availability/`, {
    method: 'POST', body: { available, reason },
  });
}

/** Every website page I may open, in the website menu's groups. */
export function fetchWebsiteMenu() {
  return apiRequest<WebsiteMenuGroup[]>('/api/website-menu/');
}

/** New task: every field's label, default and choices, as on the website. */
export function fetchNewTaskForm() {
  return apiRequest<{ fields: Record<string, FormFieldSpec> }>('/api/tasks/new/');
}

/** Creates the task; field errors come back as a 400 like the website's. */
export function createTask(values: Record<string, string | boolean>) {
  return apiRequest<{ id: number; task_number: string; warning: string }>('/api/tasks/new/', {
    method: 'POST', body: values,
  });
}

export function fetchCustomers(query = '') {
  return apiRequest<CustomerRow[]>(`/api/customers/?q=${encodeURIComponent(query)}`);
}

export function fetchCustomer(id: number) {
  return apiRequest<CustomerPage>(`/api/customers/${id}/`);
}

export function fetchMachines(query = '') {
  return apiRequest<MachineRow[]>(`/api/machines/?q=${encodeURIComponent(query)}`);
}

export function fetchMachine(id: number) {
  return apiRequest<MachinePage>(`/api/machines/${id}/`);
}

/** The Hours page for a month ("YYYY-MM"; this month by default). */
export function fetchHours(month = '') {
  return apiRequest<HoursPage>(`/api/hours/${month ? `?month=${month}` : ''}`);
}

/** Internal tasks: 'open' (default), 'closed' or 'all'; `q` searches number, subject and details. */
export function fetchInternalTickets(status = 'open', q = '') {
  return apiRequest<InternalTicket[]>(`/api/internal-tickets/?status=${status}${q ? `&q=${encodeURIComponent(q)}` : ''}`);
}

export function fetchInternalTicket(id: number) {
  return apiRequest<InternalTicketDetail>(`/api/internal-tickets/${id}/`);
}

/** Who a new internal task can be for (`me`: my own id). */
export function fetchNewInternalTicketForm() {
  return apiRequest<{ people: { value: number; label: string }[]; me: number }>('/api/internal-tickets/new/');
}

export function createInternalTicket(input: { subject: string; description: string; assigned_to: number | '' }) {
  return apiRequest<InternalTicketDetail>('/api/internal-tickets/new/', { method: 'POST', body: input });
}

/** A note ({action: 'note', message}), handing it on ({action: 'reassign', assigned_to}) or closing it ({action: 'close', result}). */
export function actOnInternalTicket(id: number, input: Record<string, string | number>) {
  return apiRequest<InternalTicketDetail>(`/api/internal-tickets/${id}/`, { method: 'POST', body: input });
}

/** One file: on my note `noteId`, or (no note) on a task I opened. */
export function uploadInternalTicketFile(id: number, fileUri: string, mimeType: string, noteId?: number | null) {
  return uploadFileTo<InternalTicketDetail>(
    `/api/internal-tickets/${id}/files/`, fileUri, mimeType, 'file', noteId ? { note: String(noteId) } : {},
  );
}

/** Where each technician last tapped today, and what needs a follow-up. */
export function fetchTeamMap() {
  return apiRequest<TeamMap>('/api/team-map/');
}

/** The manager's Dashboard, for the chosen country. */
export function fetchDashboard() {
  return apiRequest<Dashboard>('/api/dashboard/');
}

/**
 * A manager's action on a task: cancel / close / negligence (with a note),
 * or correct_time (event_id, corrected_at "YYYY-MM-DDTHH:MM", reason).
 */
export function manageTask(id: number, body: Record<string, string | number>) {
  return apiRequest<TeamTaskDetail>(`/api/tasks/${id}/manage/`, { method: 'POST', body });
}

export function fetchStaffAccounts(query = '') {
  return apiRequest<StaffAccount[]>(`/api/staff-accounts/?q=${encodeURIComponent(query)}`);
}

/** An urgent fix to someone's login; a temporary password comes back once. */
export function staffAccountAction(id: number, action: string) {
  return apiRequest<{ message: string; password: string }>(`/api/staff-accounts/${id}/`, {
    method: 'POST', body: { action },
  });
}

/** A photo of one machine on the installation checklist; returns its address. */
export function uploadLinePhoto(taskId: number, lineId: number, photo: { uri: string; name: string; type: string }) {
  const form = new FormData();
  form.append('photo', photo as unknown as Blob);
  return apiRequest<{ photo_url: string }>(`/api/my-tasks/${taskId}/lines/${lineId}/photo/`, {
    method: 'POST', body: form, isFormData: true,
  });
}

/** A follow-up installation for the machines still to install. */
export function createFollowUp(id: number) {
  return apiRequest<{ id: number; task_number: string }>(`/api/tasks/${id}/follow-up/`, { method: 'POST', body: {} });
}

/** The chain's next step (loading / installation) at a site, for the machines chosen. */
export function createNextStep(id: number, step: string, site: string, lines: number[], supervisor = '') {
  return apiRequest<{ id: number; task_number: string }>(`/api/tasks/${id}/next-step/`, {
    method: 'POST', body: { step, site, lines, supervisor },
  });
}

/** A task's planned machine lines, and whether they can be changed. */
type TaskLines = {
  locked: boolean;
  /** Why it can't be changed: done (managers only), or a loading (planned by a manager). */
  locked_reason: string;
  one_per_machine: boolean;
  is_receiving: boolean;
  /** Loading: what its warehouse holds to plan from. */
  stock: { code: string; quantity: number }[] | null;
  /** After saving a loading: more planned than the warehouse holds (not a block). */
  warning: string;
  lines: PlannedLine[];
  /** Each line's brand to pick from, and the task's own (the default). Older servers: absent. */
  brands?: { id: number; name: string }[];
  task_brand_id?: number | null;
};

export function fetchTaskLines(id: number) {
  return apiRequest<TaskLines>(`/api/tasks/${id}/lines/`);
}

/** Replaces the plan; on a loading or an installation "× 4" becomes four lines. */
export function saveTaskLines(id: number, lines: PlannedLine[], stockReason = '') {
  return apiRequest<TaskLines>(`/api/tasks/${id}/lines/`, {
    method: 'POST', body: { lines, stock_reason: stockReason },
  });
}

/** Someone who left: deactivated, their open tasks moved to a colleague. */
export function deactivatePerson(id: number, reason: string, moveTo: string) {
  return apiRequest<{ moved: number; still_to_reassign: number }>(`/api/people/${id}/deactivate/`, {
    method: 'POST', body: { reason, move_to: moveTo },
  });
}

/** Add a user (admin): every field's label, default and choices. */
export function fetchNewPersonForm() {
  return apiRequest<{ fields: Record<string, FormFieldSpec> }>('/api/people/new/');
}

export function createPerson(values: Record<string, string>) {
  return apiRequest<{ id: number; name: string; has_login: boolean }>('/api/people/new/', { method: 'POST', body: values });
}
