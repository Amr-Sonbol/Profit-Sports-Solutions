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
  language_choices?: Choice[];
  /** Countries a manager can switch the app to (empty for everyone else). */
  countries?: Choice[];
  home_country_id?: string;
  /** What this role may do (RolePermission codes) — decides which tabs show. */
  permissions?: string[];
  /** Still on a temporary password: only the "choose your password" screen shows. */
  must_change_password?: boolean;
  /** The app's language: their country's (Arabic in Egypt and Saudi Arabia). Older servers: absent. */
  app_language?: 'en' | 'ar';
  /** Does visits (technician, supervisor): has the Progress tab. Older servers: absent. */
  field_staff?: boolean;
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
  /** Who it's assigned to (the lead) — or, once done, who did it. Older servers: absent. */
  /** Team messages I haven't read yet. Older servers: absent. */
  unread_messages?: number;
  lead_name?: string;
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
  /** A manager's correction of a tap's time, if any — the time that counts. */
  corrected_at?: string | null;
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
  /** The machine it shows (taken from its card on the report); blank: the visit as a whole. */
  asset?: number | null;
  /** 'customer': a customer's photo the technician confirmed. Older servers: absent. */
  source?: string;
  uploaded_by_name?: string;
  asset_name?: string;
}

/** A customer's photo or video of a machine the technician can still confirm for its slot ("Use this"). */
export interface CustomerFile {
  name: string;
  kind: string;
  slot: 'serial_plate' | 'before';
  url: string;
  /** Added for the customer by our team (from WhatsApp...). */
  added_by_staff?: boolean;
}

/** One machine of the task as a photo card, the same on every screen (tasks.views.task_machine_cards). */
export interface MachineCardData {
  asset_id: number | null;
  /** A machine the customer reported that isn't registered yet: no photos until it is. */
  registered: boolean;
  /** What the customer reported for it: from_request.machines with this id. */
  ticket_machine_id?: number | null;
  name: string;
  serial_no: string;
  photos: TaskAttachment[];
  /** The customer's photos it can still use, each for its slot. Older servers: absent. */
  customer_files?: CustomerFile[];
}

export interface TaskDetail {
  id: number;
  /** Who created the task, and when (tasks.models.Task.creator). */
  created_by?: { name: string; role: string; at: string | null };
  /** Who last changed it (spots.audit.trail), or null. */
  last_changed?: { name: string; role: string; at: string | null } | null;
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
  machines?: { name: string; serial: string; problem: string; description: string }[];
  /** The task's machines as photo cards. Older servers: absent. */
  machine_cards?: MachineCardData[];
  /** The spare parts for the visit: from our stock, ordered and waiting, or arrived. Older servers: absent. */
  parts_needed?: import('@/api/endpoints').PartNeededRow[];
  /** The warehouse got them ready for the visit. */
  parts_prepared?: boolean;
  delivery_note_url?: string;
  events: TaskEvent[];
  attachments: TaskAttachment[];
  // The one next step in accept -> en_route -> arrive -> start, or null
  // once there isn't one (see tasks.views.TECHNICIAN_ACTIONS) — the
  // server is the only thing that decides what's next, this is never
  // computed here.
  next_action: string | null;
  // A part shipment's own steps (tasks.shipments), or null for any other
  // task: the parcel, and whether this person hands it to the courier now.
  shipment?: Shipment | null;
  // Everything from the customer's request the task came from, or null.
  from_request?: FromRequest | null;
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
  work_lines?: string[];
  recommendations?: string[];
  recommendation?: string;
}

/**
 * One machine on the report: one at the site (asset_id), or one to
 * register (brand_id + model_name), which may be a customer's ticket
 * machine that wasn't in their list (ticket_machine_id).
 */
export interface ReportMachine {
  asset_id?: number;
  name?: string;
  /** This machine's own photos on the task (shown under its card; never sent back). */
  photos?: TaskAttachment[];
  ticket_machine_id?: number | null;
  brand_id?: number | null;
  /** The brand as typed: a known one counts even when not picked from the suggestions. */
  brand_name?: string;
  model_name?: string;
  serial_no: string;
  /** The product code (e.g. BENCH-01), beside the serial — optional. */
  code?: string;
  /** A new machine with no readable plate — left out on purpose, and flagged. */
  no_serial?: boolean;
  /** The customer's photo of the plate, from their ticket. */
  serial_photo_url?: string;
  outcome: string;
  /** The finding when none of the standard ones fits. */
  note: string;
  // The report's standard wording (reports.phrases): codes, shown in the customer's language.
  finding_code?: string;
  action_code?: string;
  action?: string;
}

export interface SiteMachine {
  id: number;
  name: string;
  serial_no: string;
  photos?: TaskAttachment[];
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
  /** An installation's on-site checklist (without its photo address). */
  checklist?: Omit<ChecklistRow, 'photo_url' | 'added_on_site'>[];
  findings: string;
  action_taken: string;
  // Standard work lines and recommendations (codes), and a typed one.
  work_lines?: string[];
  recommendations?: string[];
  recommendation?: string;
  resolved: boolean;
  // Ignored by the server now — the visit's hours come from the taps.
  labour_hours?: string;
  // When it was filed: a report saved offline is sent later, and the
  // visit's time stops at filing, not at sending.
  filed_at?: string;
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

/** A page of the request list: the tab's rows, the badge counts, whether more follow. */
/** One person on Team → Today (tasks.views.team_today). */
export interface TeamTodayRow {
  id: number;
  name: string;
  role: string;
  photo_url: string;
  phone: string;
  state: 'blocked' | 'working' | 'on_site' | 'paused' | 'on_the_way' | 'free' | 'abroad' | 'off';
  off_reason: string;
  abroad: { country: string; until: string } | null;
  visiting_from: string;
  since: string | null;
  task: { id: number; task_number: string; customer: string; site: string; scheduled_for: string | null } | null;
  next: { id: number; task_number: string; customer: string; site: string; scheduled_for: string | null } | null;
  today_total: number;
  today_done: number;
  location: { lat: number; lng: number; away: boolean } | null;
}

export interface TicketPage {
  counts: { new: number; escalated: number };
  count: number;
  page: number;
  has_more: boolean;
  results: (Ticket & { escalated: boolean })[];
}

export interface TicketMachine {
  id: number;
  name: string;
  serial: string;
  problem: string;
  urgent: boolean;
  description: string;
  items: RequestFile[];
  /** '' while waiting for "What happens next?". */
  decision: string;
  decision_display: string;
  decision_note: string;
  task: { id: number; task_number: string } | null;
}

export interface TicketDetail {
  id: number;
  ticket_number: string;
  status: string;
  status_display: string;
  escalated: boolean;
  submitted_at: string;
  /** The person at the customer who sent it from the portal; '' when staff opened it. */
  sent_by?: string;
  company_name: string;
  customer_name: string;
  site_name: string;
  site_address: string;
  has_branch: boolean;
  country: string;
  contact_name: string;
  contact_phone: string;
  contact_email: string;
  about: string;
  need: string;
  subject: string;
  /** Their message, when the request has no machines. */
  message: string;
  machines: TicketMachine[];
  other_files: RequestFile[];
  conversation: { from_us: boolean; name: string; message: string; sent_at: string; files: RequestFile[] }[];
  notes: { name: string; message: string; created_at: string; files: RequestFile[] }[];
  escalation: { to: string; by: string; reason: string; at: string } | null;
  tasks: { id: number; task_number: string; status_display: string }[];
  close_reason: string;
  can_manage: boolean;
  can_reply: boolean;
  /** The country does visits (not the USA); shipping suggested (no visits, or the customer's own team). Older servers: absent. */
  does_visits?: boolean;
  suggest_ship?: boolean;
  /** "What happens next?" — only while new and for whoever handles requests. */
  next: {
    supervisors: { value: number; label: string }[];
    /** null: this person can't pick the technician — the supervisor will. */
    technicians: { value: number; label: string }[] | null;
    reasons: { group: string; options: { value: string; label: string; suggestion: string }[] }[];
    task_type: string;
    urgent: boolean;
    under_warranty: boolean;
  } | null;
  website_path: string;
}

export interface TicketDecision {
  choice: 'visit' | 'ship' | 'answer';
  machines: number[];
  supervisor?: number | '';
  technician?: number | '';
  reason?: string;
  answer?: string;
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
/** What this person can do on the task now, as the website's task page offers it. */
export interface TaskActions {
  /** The visit's day and time can be set here (else, when a manager fixed it: request_schedule_change). */
  schedule?: boolean;
  has_contact_email: boolean;
  notify_schedule: boolean;
  schedule_notified_at: string | null;
  notify_delay: boolean;
  notify_shipping: boolean;
  set_schedule_time: boolean;
  scheduled_date: string | null;
  request_schedule_change: boolean;
  pending_change: { id: number; requested_for: string; reason: string; requested_by: string; can_review: boolean } | null;
  rebook: boolean;
  send_feedback_request: boolean;
  feedback_requested_at: string | null;
  reopen: boolean;
}

/** Who reviewed or approved a report (tasks.models.Task.approvals). */
export interface Approval {
  name: string;
  role: string;
  at: string;
  instead: boolean;
}

export interface TeamTaskDetail extends TaskDetail {
  /** Older servers: absent. */
  actions?: TaskActions;
  /** Parts needed: its owner changes them; the warehouse marks them prepared once all are in. */
  can_change_parts?: boolean;
  /** Correcting a part: managers only. */
  can_edit_parts?: boolean;
  can_prepare_parts?: boolean;
  lead: Assignment | null;
  helpers: Assignment[];
  report: WorkReport | null;
  responsible_supervisor_name: string;
  can_assign: boolean;
  can_supervisor_approve: boolean;
  can_manager_approve: boolean;
  approvals?: { reviewed: Approval | null; approved: Approval | null; waiting: 'review' | 'approval' | null };
  /** An equipment task's machines, the chain around it, and the follow-up offer. */
  equipment?: {
    lines: {
      product_code: string; kind: string; quantity: number; serial_number: string; status: string; status_label: string;
      /** Its brand (else the task's); older servers: absent. */
      brand?: string;
      colors: string;
      replacement: string; note: string; added_on_site: boolean; photo_url: string;
    }[];
    still_to_install: number;
    can_follow_up: boolean;
    previous_task: { id: number; task_number: string } | null;
    next_tasks: { id: number; task_number: string }[];
    /** Loading: what can be sent on to an installation, and where — the manager tier only. */
    next_step: {
      /** The button's words: "Send to installation". */
      title: string;
      lines: { id: number; label: string }[];
      steps: { value: string; label: string; sites: (Choice & { country?: number })[] }[];
      /** Who can supervise it there; empty keeps this task's own (same country only). Older servers: absent. */
      supervisors?: (Choice & { country: number })[];
    } | null;
  } | null;
  /** A manager's actions here (api/views.py TaskManageView). */
  manage?: { can_cancel: boolean; can_close: boolean; can_flag: boolean; correctable_event_ids: number[] };
  /** An installation's site checked ready before the truck goes; null for any other task. */
  site_readiness?: SiteReadiness | null;
}

export interface CataloguePart {
  code: string;
  description: string;
  /** What the part looks like (the Parts screen's photo); '' without one. */
  photo_url: string;
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
  /** A supervisor's tasks still to plan: no day or no lead yet (older servers: absent). */
  to_plan?: PlanItem[];
}

export interface PlanItem extends ScheduleItem {
  missing: ('date' | 'lead')[];
  received_at: string;
  lead_name: string;
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
  /** Their profile photo, or '' (initials). Older servers: absent. */
  photo_url?: string;
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

/** One skill or conduct area on My skills (api/views.py MySkillsView). */
export interface SkillRow {
  kind: 'skill' | 'conduct';
  id: number;
  name: string;
  level: number | null;
  level_label: string;
  // 'none' (not rated), 'self' (waiting for supervisor review) or 'confirmed'.
  status: 'none' | 'self' | 'confirmed';
  confirmed_by: string;
  note: string;
  evidence_url: string;
  can_rate: boolean;
}

export interface MySkills {
  progress: {
    basic_confirmed: number;
    basic_total: number;
    advanced_confirmed: number;
    advanced_total: number;
    conduct_confirmed: number;
    conduct_total: number;
    waiting: number;
    is_certified: boolean;
    reliable_level: number;
  };
  levels: { level: number; short: string; text: string }[];
  basic: SkillRow[];
  advanced: SkillRow[];
  conduct: SkillRow[];
}

/** Someone on the viewer's team (api/views.py PersonView). */
export interface Person {
  id: number;
  name: string;
  role: string;
  photo_url: string;
  phone: string;
  email: string;
  country: string;
  is_available: boolean;
  unavailable_reason: string;
  unavailable_reasons: Choice[];
  can_review_skills: boolean;
  can_set_availability: boolean;
  /** A manager can deactivate them, handing their open tasks to a colleague. */
  can_deactivate?: boolean;
  open_tasks?: string[];
  move_to_choices?: Choice[];
  skills: MySkills;
  record: { completed: number; helped: number; on_time_percent: number | null; acceptance_minutes: number | null };
  /** The website's technician page (tasks.views.technician_overview). Older servers: absent. */
  workload?: { today: number; upcoming: number; overdue: number };
  visits_this_month?: number;
  rating_average?: number | null;
  rating_count?: number;
  tasks?: (PersonTaskRef & { my_role: string; still_on_it: boolean })[];
  reports?: (PersonTaskRef & { submitted_at: string; resolved: boolean; rating: number | null; comment: string })[];
  trips?: { country: string; start: string; end: string; note: string }[];
}

interface PersonTaskRef {
  id: number;
  task_number: string;
  customer: string;
  site: string;
  status: string;
  status_display: string;
  scheduled_for: string | null;
}

/** The More tab: the website's menu (api/views.py WebsiteMenuView). */
export interface WebsiteMenuGroup {
  heading: string;
  pages: { title: string; url: string; icon: string }[];
}

/** One field of the New task form (api/views.py TaskCreateView). */
export interface FormFieldSpec {
  label: string;
  required: boolean;
  initial: string;
  choices?: Choice[];
}

/** Customers, sites and machines (api/views.py CustomersView, CustomerView, MachinesView, MachineView). */
export interface CustomerRow {
  id: number;
  name: string;
  segment: string;
  /** Our own account — its sites are our warehouses. */
  is_internal?: boolean;
  sites: string[];
  contact_name: string;
  contact_phone: string;
}

export interface HistoryTask {
  id: number;
  task_number: string;
  status_display: string;
  reported_at: string;
  site_name: string;
  task_type_name: string;
}

export interface MachineRow {
  id: number;
  name: string;
  serial: string;
  status: string;
  customer_id: number;
  customer_name: string;
  site_name: string;
}

export interface CustomerPage {
  id: number;
  name: string;
  segment: string;
  is_internal?: boolean;
  contact_name: string;
  contact_phone: string;
  contact_email: string;
  billing_address: string;
  has_portal: boolean;
  sites: {
    id: number; name: string; address: string; contact_name: string; contact_phone: string;
    access_notes: string; directions_url: string;
    machines: { id: number; name: string; serial: string; status: string }[];
  }[];
  tasks: HistoryTask[];
}

export interface MachinePage extends MachineRow {
  installed_on: string | null;
  /** The product code (e.g. BENCH-01), beside the serial; '' if not recorded. */
  code: string;
  history: (HistoryTask & { outcome: string; finding: string; action: string })[];
}

/** The Hours page (api/views.py HoursView). Hours as "1:32". */
export interface HoursPage {
  month: string;
  prev_month: string;
  next_month: string;
  is_this_month: boolean;
  total_hours: string;
  total_reports: number;
  rows: {
    id: number; name: string; reports_count: number; hours: string; estimated: string; helped_on: number;
    overrun_count: number; above_tapped_count: number; away_count: number;
    reports: { task_id: number; task_number: string; submitted_at: string; hours: string; customer_name: string }[];
  }[];
}

/** An internal ticket (api/views.py InternalTicketsView). */
export interface InternalTicket {
  id: number;
  ticket_number: string;
  subject: string;
  description: string;
  status_display: string;
  submitted_at: string;
  assigned_to: string;
}

/** One internal task, as its screen shows it (tasks.views.internal_ticket_for_app). */
export interface InternalTicketDetail {
  id: number;
  ticket_number: string;
  status: string;
  status_display: string;
  is_open: boolean;
  // Notes, handing on, closing: the person it's for, the opener, or someone stepping in.
  can_act: boolean;
  subject: string;
  description: string;
  assigned_to: { id: number; name: string } | null;
  opened_by: string;
  submitted_at: string;
  files: RequestFile[];
  notes: { id: number; name: string; mine: boolean; message: string; created_at: string; files: RequestFile[] }[];
  close_reason: string;
  task: { id: number; task_number: string } | null;
  can_convert: boolean;
  /** Who it can be handed to (open ones only). */
  people: { value: number; label: string }[];
  website_path: string;
  /** After adding a note: its id, for the files that go with it. */
  note_id?: number | null;
}

/** The team map (api/views.py TeamMapView). */
export interface TeamMap {
  pins: { name: string; detail: string; place: string; lat: number; lng: number; away: boolean; task_id: number }[];
  attention: { text: string; level: 'critical' | 'warning'; task_id: number }[];
  without_taps: string[];
  /** Where the map opens before anyone has tapped: the middle of the country's branches. Older servers: absent. */
  area?: { latitude: number; longitude: number; delta: number } | null;
}

/** The manager's Dashboard (api/views.py DashboardView). */
export interface Dashboard {
  country: string;
  /** The decisions waiting on me (tasks.views.waiting_for). Older servers: absent. */
  waiting?: import('@/api/endpoints').WaitingItem[];
  actions: { label: string; count: number | null; level: 'critical' | 'warning'; url: string }[];
  /** The whole company at a glance, for the Technical Support and Operations Managers (tasks.alarms.company_summary). */
  company_summary?: {
    blocked: number; blocked_long: number; unscheduled_overdue: number; new_tasks_today: number;
    reports_to_review: number; reports_to_approve: number; reports_late: number;
    new_requests: number; escalated_requests: number; locked_staff: number; locked_customers: number;
  } | null;
  follow_ups: { text: string; level: 'critical' | 'warning'; task_id: number }[];
  stats: {
    available_count: number; technician_count: number; open_task_count: number;
    unassigned_count: number; unscheduled_count: number; emergency_count: number;
  };
  tasks: {
    id: number; task_number: string; customer_name: string; site_name: string; status_display: string;
    priority: string; priority_display: string; lead: string; scheduled_for: string | null; scheduled_date: string | null;
  }[];
}

/** A staff login (api/views.py StaffAccountsView). */
export interface StaffAccount {
  id: number;
  name: string;
  role: string;
  username: string;
  status: string;
  status_display: string;
  has_login: boolean;
  is_active: boolean;
  can_manage: boolean;
  /** A temporary password: the Admin's alone. Older servers: absent. */
  can_set_password?: boolean;
}

/** An installation's site checked ready before the truck goes (api/views.py TaskSiteReadinessView). */
export interface SiteReadiness {
  space_and_floor: boolean;
  power: boolean;
  access: boolean;
  date_confirmed: boolean;
  note: string;
  is_ready: boolean;
  checked_by: string;
  checked_at: string | null;
  can_check: boolean;
}

/** One line on an equipment step's checklist (api/views.py MyTaskReportView). */
export interface ChecklistRow {
  /** The planned line; null for one added on site. */
  id: number | null;
  product_code: string;
  /** 'machine' or 'part' (a spare part — receiving only). */
  kind: string;
  /** Receiving: how many are listed (1 per machine on loading and installation). */
  quantity: number | string;
  /** Receiving, when fewer arrived than listed: how many did, and what happened to the rest. */
  received?: string;
  rest_status?: string;
  /** '' until checked: done (installed), missing, damaged, wrong_item, returned, later. */
  status: string;
  serial_number: string;
  /** Loading: the sticker couldn't be reached (packed) — said instead of the serial. */
  serial_unreachable?: boolean;
  /** Installation: assembled, levelled and tested — working (needed for an installed machine). */
  tested_working?: boolean;
  /** Installation: its serial wasn't taken at loading — read it from the sticker now. Older servers: absent. */
  serial_not_taken_at_loading?: boolean;
  frame: string;
  arm: string;
  padding: string;
  trim: string;
  replacement: string;
  note: string;
  photo_url: string;
  added_on_site: boolean;
}

/** A planned machine line (api/views.py TaskLinesView). */
export interface PlannedLine {
  /** The machine already on the task (kept, corrected in place — never removed); none on a new row. */
  line_id?: number;
  /** 'machine', or 'part' (a spare part — receiving only: no serial, no colours). */
  kind: string;
  product_code: string;
  /** Its brand (the task's when none of its own); sent back as `brand`. Older servers: absent. */
  brand_id?: number | null;
  brand_name?: string;
  brand?: number | string | null;
  /** A spare part ordered for one repair: that task's number — held for it in stock. */
  for_task?: string;
  serial_number: string;
  quantity: number | string;
  frame: string;
  arm: string;
  padding: string;
  trim: string;
}

export interface Shipment {
  stage: 'preparing' | 'ready' | 'shipped' | 'cancelled';
  label: string;
  parts: { code: string; quantity: number }[];
  pak_reference_number: string;
  shipping_company: string;
  shipping_tracking_number: string;
  can_ship: boolean;
  companies: { value: string; label: string }[];
  // Where it goes and who takes it.
  send_to: {
    customer: string; branch: string; address: string; directions_url: string;
    contact_name: string; contact_phone: string;
  };
  /** The office's steps (older servers: absent): the warehouse manager prepares it, then the fitting visit. */
  can_prepare?: boolean;
  shippers?: { id: number; name: string }[];
  shipper?: string;
  can_book_fitting?: boolean;
  /** From the factory straight to the customer (no stock of ours); the only way where the country holds none. */
  can_ship_direct?: boolean;
  direct_only?: boolean;
  ships_direct?: boolean;
}

// One photo, video or file the customer sent (tasks.customer_report). `key`
// stays the same while `url` (a signed link) changes: the photo is cached once.
export interface RequestFile {
  kind: 'image' | 'video' | 'file';
  url: string;
  key: string;
  name: string;
  serial_plate: boolean;
  added_by_staff: boolean;
}

export interface ReportedMachine {
  /** Its request machine: the task's machine card with the same id shows it. Older servers: absent. */
  ticket_machine_id?: number;
  name: string;
  serial: string;
  problem: string;
  urgent: boolean;
  description: string;
  items: RequestFile[];
}

export interface FromRequest {
  ticket_number: string;
  site_name: string;
  site_address: string;
  contact_name: string;
  contact_phone: string;
  contact_email: string;
  subject: string;
  message: string;
  submitted_at: string;
  machines: ReportedMachine[];
  other_files: RequestFile[];
  sent_later: RequestFile[];
  photo_count: number;
  video_count: number;
}
