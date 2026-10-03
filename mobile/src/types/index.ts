// Mirrors api/serializers.py — keep these in sync by hand; there's no
// shared schema between the Django API and this app.

export interface Me {
  full_name: string;
  role: string | null;
  role_display?: string;
  country?: string;
  language?: string;
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
  estimated_hours: string | null;
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
  submitted_at: string;
  parts_used: PartUsed[];
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
}

export interface Ticket {
  id: number;
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
