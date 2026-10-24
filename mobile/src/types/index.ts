// Mirrors api/serializers.py — keep these in sync by hand; there's no
// shared schema between the Django API and this app.

export interface Me {
  full_name: string;
  username?: string;
  email?: string;
  phone?: string;
  /** Their profile photo, or '' for none. */
  photo_url?: string;
  role: string | null;
  role_display?: string;
  employment_type_display?: string;
  country?: string;
  language?: string;
  /** What this role may do (RolePermission codes) — decides which tabs show. */
  permissions?: string[];
  /** Still on a temporary password: only the "choose your password" screen shows. */
  must_change_password?: boolean;
}

export interface PasswordInput {
  old_password?: string;
  new_password1: string;
  new_password2: string;
}

export interface TaskListItem {
  id: number;
  task_number: string;
  site_name: string;
  customer_name: string;
  status: string;
  status_display: string;
  priority: string;
  priority_display: string;
  scheduled_for: string | null;
  promised_at: string | null;
  reported_at: string;
}

export interface TaskEvent {
  id: number;
  event_type: string;
  event_type_display: string;
  occurred_at: string;
  actor_name: string;
  note: string;
}

export interface TaskAttachment {
  id: number;
  url: string;
  media_type: string;
  media_type_display: string;
  purpose: string;
  purpose_display: string;
  uploaded_at: string;
}

export interface TaskDetail {
  id: number;
  task_number: string;
  site_name: string;
  site_address: string;
  customer_name: string;
  currency_code: string;
  status: string;
  status_display: string;
  priority: string;
  priority_display: string;
  task_type_name: string;
  brand_name: string;
  required_skill_name: string;
  description: string;
  reported_at: string;
  promised_at: string | null;
  scheduled_for: string | null;
  scheduled_date?: string | null;
  estimated_hours: string | null;
  // Who to call on site and how to get there.
  site_contact_name?: string;
  site_contact_phone?: string;
  directions_url?: string;
  // The machines the customer reported for this task (portal ticket).
  machines?: { name: string; serial: string; problem: string; out_of_service: boolean; description: string }[];
  events: TaskEvent[];
  attachments: TaskAttachment[];
  // The one next step in accept -> en_route -> arrive -> start, or null
  // once there isn't one (see tasks.views.TECHNICIAN_ACTIONS) — the
  // server is the only thing that decides what's next, this is never
  // computed here.
  next_action: string | null;
  is_lead: boolean;
  can_file_report: boolean;
  // The task type's own setting — the API refuses a report without one.
  requires_signature: boolean;
  // The lead's own last tap ("Accepted", "Arrived", ...) while it can still
  // be undone — 10 minutes, nothing since. Null otherwise.
  undoable_step: string | null;
}

export interface PartUsed {
  part_code: string;
  description: string;
  quantity: number;
  unit_cost: string;
  currency_code: string;
}

export interface WorkReport {
  findings: string;
  action_taken: string;
  resolved: boolean;
  labour_hours: string;
  customer_name: string;
  signature_url: string;
  signature_waived_reason: string;
  submitted_at: string;
  parts_used: PartUsed[];
  copy_sent_to?: string;
  copy_sent_at?: string | null;
}

/**
 * One machine on the report: one at the site (asset_id), or one to
 * register (brand_id + model_name), which may be a customer's ticket
 * machine that wasn't in their list (ticket_machine_id).
 */
export interface ReportMachine {
  asset_id?: number;
  name?: string;
  ticket_machine_id?: number | null;
  brand_id?: number | null;
  model_name?: string;
  serial_no: string;
  outcome: string;
  note: string;
}

export interface SiteMachine {
  id: number;
  name: string;
  serial_no: string;
}

export interface Brand {
  id: number;
  name: string;
}

export interface Choice {
  value: string;
  label: string;
}

/** A helper on the job — their hours default to the report's own. */
export interface ReportHelper {
  id: number;
  technician_name: string;
  labour_hours: string | null;
}

export interface WorkReportInput {
  findings: string;
  action_taken: string;
  resolved: boolean;
  labour_hours: string;
  customer_name: string;
  parts: PartUsed[];
  // A PNG data URL from the signature pad; null keeps the one on file.
  signature: string | null;
  // Filled in when the customer wasn't there to sign.
  signature_waived_reason?: string;
  // Helper assignment id -> hours; empty means the same as labour_hours.
  helper_hours?: Record<string, string>;
  // Where the customer's PDF copy is emailed on the first submission; blank sends nothing.
  send_copy_to?: string;
  // The machines this visit covered; left out, the server keeps what it has.
  machines?: ReportMachine[];
  // Where the phone was when Submit was pressed (src/location.ts) — kept
  // with a report waiting offline, so it's where it was filled in.
  latitude?: number;
  longitude?: number;
  accuracy?: number | null;
}

export interface Ticket {
  id: number;
  ticket_number: string;
  company_name: string;
  site_description: string;
  country_name: string;
  status: string;
  status_display: string;
  contact_name: string;
  contact_phone: string;
  description: string;
  submitted_at: string;
}

export interface ApiError {
  detail?: string;
  [field: string]: unknown;
}

export interface Assignment {
  id: number;
  technician_id: number;
  technician_name: string;
  role: 'lead' | 'helper';
  assigned_at: string;
}

// A supervisor's/manager's view of any task in scope (api/views.py
// TeamTaskDetailView). The can_* flags are decided by the server.
export interface TeamTaskDetail extends TaskDetail {
  lead: Assignment | null;
  helpers: Assignment[];
  report: WorkReport | null;
  responsible_supervisor_name: string;
  can_assign: boolean;
  can_supervisor_approve: boolean;
  can_manager_approve: boolean;
}

export interface CataloguePart {
  code: string;
  description: string;
}

export interface Candidate {
  id: number;
  full_name: string;
  is_available: boolean;
  // Their home country when they're working here on a trip, else null.
  visiting_from: string | null;
  skill_level: number | null;
  next_task_number: string | null;
  next_task_at: string | null;
}

/** A task on the Today screen (/api/my-today/). */
export interface ScheduleItem extends TaskListItem {
  my_role: 'lead' | 'helper' | 'responsible' | '';
  address: string;
  scheduled_date: string | null;
  estimated_finish: string | null;
}

export interface TodayPlan {
  today: string;
  today_count: number;
  overdue: ScheduleItem[];
  next_job: (ScheduleItem & { next_action: string | null; directions_url: string }) | null;
  later_today: ScheduleItem[];
  done_today: ScheduleItem[];
}

/** My progress (/api/my-progress/): the year's line and a month's figures. */
export interface MyProgress {
  trend: {
    months: { start: string; completed: number }[];
    top: number;
    this_month: number;
    change: number | null;
  };
  month: { first: string; is_this_month: boolean; hours: string | number; reports: number; over_estimate: number };
  rank: number | null;
  leaderboard_size: number;
  certification: { certified: number; required: number; is_certified: boolean };
}

/** One person on Team progress (/api/team-progress/). */
export interface TeamProgressRow {
  id: number;
  name: string;
  role: string;
  rank: number | null;
  completed: number;
  change: number;
  hours: string | number;
  certified: number;
  required: number;
  is_certified: boolean;
  months: number[];
}

/** One day of the schedule calendar (/api/my-schedule/). */
export interface ScheduleDay {
  date: string;
  is_today: boolean;
  is_weekend: boolean;
  in_month: boolean;
  week_start: string;
  tasks: ScheduleItem[];
}

/** One day of a small month on the year view. */
export interface ScheduleYearCell {
  date: string;
  count: number;
  is_today: boolean;
  is_weekend: boolean;
  week_start: string;
}

/** Week, month or year of someone's tasks — the website's My schedule. */
export interface MySchedule {
  view: 'week' | 'month' | 'year';
  today: string;
  person: { id: number; name: string };
  overdue: ScheduleItem[];
  unscheduled: ScheduleItem[];
  // week
  start?: string;
  end?: string;
  prev_start?: string;
  next_start?: string;
  this_week_start?: string;
  // month
  month?: string;
  prev_month?: string;
  next_month?: string;
  this_month?: string;
  // week and month
  days?: ScheduleDay[];
  // year
  year?: number;
  months?: { first: string; count: number; cells: (ScheduleYearCell | null)[] }[];
}
