# Profit Sports Solutions — Technician App
## Database design v2

15 tables. Target stack: Django + PostgreSQL, one developer.

---

## What this app does

A supervisor receives a customer complaint, creates a task with whatever he knows, and assigns it to one or more technicians. The technician sees it on his phone, goes to the site, does the work, and reports what he found. The office reviews the report.

That is the whole system. Everything below serves that.

---

## Arabic and English from the first commit

Not a later phase. Retrofitting right-to-left means touching every screen.

- **It is mirroring, not translation.** The layout flips — back arrow points right, badges move left, icons sit right of their labels. Use `padding-inline-start` and `margin-inline-end`, never `padding-left` or `margin-right`. Written the wrong way, converting later is a week of tedious work.
- **`language` goes on the technician, not the country.** Some Saudi staff prefer English, some Egyptian staff Arabic. It is a personal setting.
- **Codes stay left-to-right inside Arabic text.** Task numbers, serials, model codes, phone numbers all need `dir="ltr"` on the element. This is the most common Arabic bug, and it looks broken to Arabic readers while looking fine to a developer who does not read Arabic.
- **Western digits, not Arabic-Indic.** 012, not ٠١٢. Avoids trouble where serials and part codes mix with numbers.
- **Warranty claims need a decision.** If findings are written in Arabic and the claim goes to a principal in Europe, someone must translate. Either require English fault descriptions on warranty tasks, or translate at claim time in the office.

---

## Five rules

1. **Store all timestamps in UTC.** Convert at display. The region spans four time zones.
2. **Almost every field on a task is nullable at creation.** The supervisor rarely knows the machine, model, or fault in advance. Forcing him to answer produces guesses, and a guessed brand is worse than a blank one.
3. **The technician records the truth in his report.** Brand, model, serial, fault. He is the first person who actually sees the machine.
4. **Never delete a record that has history.** Set `is_active = false`. Deleting a brand, country, or technician breaks every task that references it.
5. **Every task has one lead.** Group work still needs one accountable person.

---

## 1. Reference

### country
| Column | Type | Notes |
|---|---|---|
| id | PK | |
| name | varchar | |
| name_ar | varchar | |
| iso_code | char(2) | the real ISO 3166-1 code — never changed for this |
| task_prefix | varchar | nullable — overrides `iso_code` as the task-number prefix |
| timezone | varchar | IANA name, e.g. `Asia/Riyadh` |
| currency_code | char(3) | |
| is_active | bool | |

**`task_prefix` exists because the company doesn't use ISO codes at all.** Every seeded country has its own locally-used abbreviation set here — EGY, BAH, QAT, UAE, KSA, OMN, USA, CAN, KWT — none of them the two-letter ISO code. `iso_code` itself is left alone regardless: it stays the real ISO code, in case something else ever needs it to actually be one, and new countries still need `task_prefix` filled in explicitly (it has no default) or their task numbers fall back to that ISO code.

**Changing a country's `task_prefix` only affects tasks created afterward.** `_next_task_number` counts existing tasks that already start with the new prefix, which is always zero right after a change — task numbering restarts at 1 under the new prefix rather than continuing the old sequence, and every task number ever issued keeps the prefix it was created with.

### brand
Panatta, Skillcore, Digilock, and any future principal. Available in all countries.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| name | varchar | |
| portal_url | varchar | where the office requests spare parts |
| is_active | bool | |

No brand name is ever hardcoded in the application. Adding a principal is an office action.

### skill
**One skill per repair task — brand-agnostic.** Replacing a pin is the same skill whatever brand it's on; what a technician is actually rated on is whether they can do that specific job, not "how good are they at Panatta in general." This replaced an earlier version of the table where each brand carried its own single skill row (plus a second `Cardio` row for the two brands that needed one) — that framing didn't match how the business evaluates competence, so it's gone.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| name | varchar | |
| name_ar | varchar | |
| category | varchar | `other` (shown as "Basic") or `cardio` — see below |
| is_active | bool | |

Bilingual like `task_type`/`conduct_area` — a `display_name` property picks `name_ar` when Arabic is active, same pattern, same reasoning.

**Seeded with 15 basic-level skills** (daily visual inspection, replacing pins/rubbers/covers/springs/cables/the platform, tightening bolts, ...) that every technician needs regardless of brand, **plus 20 cardio skills** split across treadmill internals (belt, deck, motor, MCB, incline motor, rollers, drive belt, console, safety key, wiring), bike internals (pedals, crank arms, resistance unit, flywheel bearing, drive belt/chain, recumbent seat rail), and elliptical internals (transmission belt, ventilation fan and manual pulse sensors, footplates and handgrips, rear flywheel and its bearing). Being a certified technician requires confirmed competence on every *basic* skill; cardio competence is excluded from that bar and instead marks readiness for the supervisor track (see "How a level gets set," below) — same `other`/`cardio` split as before, just populated with tasks instead of brands.

**A manager can add more from the Skills screen** (`/tasks/skills/`, manager-only — same fixed-floor reasoning as `role_permissions`: this is global reference data, not scoped to any country, and letting the screen that manages it be gated by its own permission row risks a bad edit locking every role out of fixing it). Deactivating an existing skill is still a Django admin action, same as brands and task types.

**The old brand-based rows aren't deleted, just deactivated.** `technician_skill`/`technician_skill_assessment` rows still reference them with `on_delete=PROTECT`, and the project's own rule is never delete a record with history — they simply drop off every screen (`is_active=False`) and stop counting toward anything.

### conduct_area
The non-technical half of the certification bar — cleanliness, procedure adherence, and the other things that make the difference between a technician and a professional. Not tied to any brand.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| name | varchar | |
| name_ar | varchar | |
| is_active | bool | |

Seeded with six areas: cleanliness & site care, professional appearance & conduct, rule & procedure adherence, punctuality & communication, tool & vehicle care, adherence to official uniform. Rated the same way, on the same 1–4 scale, as a skill — see `technician_conduct` below.

### task_type
A managed list the office maintains. Never free text — free text becomes "repair", "Repair", "fixing" within a year.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| code | varchar | stable key, never changes |
| name | varchar | |
| name_ar | varchar | |
| category | varchar | `installation` or `maintenance` |
| requires_photos | bool | |
| requires_signature | bool | |
| checklist_template | jsonb | the fields the report form shows for this type |
| is_active | bool | |

`checklist_template` is the leverage: one report screen renders whatever the type defines, so dozens of task types need only one screen.

---

## 2. Customers and machines

### customer
| Column | Type | Notes |
|---|---|---|
| id | PK | |
| country_id | FK → country | |
| name | varchar | |
| code | varchar | optional — this company's own account/reference code, if it has one |
| segment | varchar | gym, hotel, club, other |
| contact_name | varchar | optional — the single-branch case, or a chain sharing one contact |
| contact_phone | varchar | |
| contact_email | varchar | |
| shipping_address | text | optional — where replacement parts should be delivered, if different from the site itself (a warehouse or head office) |
| language | varchar(2) | ar, en — default en; drives the portal's language/direction once logged in |
| user_id | FK → user | nullable — the customer's own portal login, one per customer, created by staff |
| is_active | bool | |

**`user` is a real login (staff-created, never self-signup), covering every site under that customer.** Logged in, they land on their own portal — every ticket they've submitted (`customer_ticket.customer`) and a summary-only service history (date, site, task type, status — never the report detail, technician names, or parts staff see on the same task). `home` (`spots/views.py`) routes a `hasattr(user, 'customer')` login there, the same way it routes a technician to their own week; nothing else in the app is reachable with a customer login, same fixed boundary `require_technician`/`require_customer` both enforce for their own side.

**The portal has its own login page** (`/customers/portal/login/`, branded "Customer portal" rather than the plain staff one) with a language switcher for the not-yet-authenticated case (Django's built-in `set_language`) — once logged in, `language` above takes over via `TechnicianLocaleMiddleware`, the same middleware that already drives a technician's. It rejects a staff login typed in there by mistake ("This isn't a customer account"), and the shared `/accounts/login/` still works for a customer too — `home`'s routing doesn't care which page authenticated them. Self-service password reset (Django's built-in `PasswordResetView` et al., emailing `user.email` — backfilled from `contact_email` when the login is created) is available from both login pages, for any account with an email on file, not just customers.

### site
A hotel group is one customer with many sites.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| customer_id | FK → customer | |
| name | varchar | |
| address | text | |
| contact_name | varchar | |
| contact_phone | varchar | |
| contact_email | varchar | nullable — where a feedback request goes; not always known |
| access_notes | text | gate codes, best hours |

**A blank site contact falls back to the customer's own** (`Site.effective_contact_name`/`_phone`/`_email`) — every notification send (schedule confirmation, delay notice, shipping notice, feedback request) reads through these, not the raw columns, so a single-branch customer or a chain sharing one contact only has to enter it once, at the customer level.

**A new customer or site can be added two ways.** From the Customers screen directly (add a customer, then a site under it) — the way to register a whole list of gyms/hotels before any of them have a task yet — or inline while creating a task ("+ Add new site"), which only offers a new site under an *existing* customer. Both paths write the same rows; there's no separate "customer request" concept.

### asset
One physical machine. **Created by the technician at the first service visit, not at installation.**

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| site_id | FK → site | |
| brand_id | FK → brand | |
| model_name | varchar | free text from the plate |
| serial_no | varchar | nullable |
| installed_on | date | nullable |
| warranty_end | date | nullable |
| status | varchar | active, faulty, retired |

**Why the technician creates it.** Nobody else sees the machine. The delivery note is a PDF with model codes and quantities, not serials, so there is nothing to import. The serial is only on the plate.

The serial is needed when requesting spare parts on the principal's portal — which happens at service time, not installation. So it is captured exactly when it is needed, by someone standing in front of the machine.

**The trade-off, stated plainly.** Until a machine has been serviced once it is not in your system, so you cannot tell whether the broken treadmill is the same one that failed last year. After a year of normal work, the machines that matter — the ones that break — will all be recorded.

---

## 3. People

### technician
Covers technicians, supervisors, and managers. One table, different roles.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| user_id | FK → auth user | for Microsoft SSO later |
| country_id | FK → country | |
| full_name | varchar | |
| phone | varchar | |
| language | varchar | `ar` or `en` — per person, not per country |
| role | varchar | technician, supervisor, manager |
| employment_type | varchar | staff, freelance |
| has_transport | bool | |
| can_carry_large | bool | can move a treadmill motor or locker bank |
| hired_on | date | |
| is_active | bool | |
| is_available | bool | can currently be assigned work — separate from `is_active` |
| unavailable_reason | varchar | sick, leave, holiday, other — set when `is_available` is false |
| photo | file | nullable — a headshot, shown on the roster, boards, and task detail |

**`is_active` is employment; `is_available` is today.** A technician stays `is_active` for as long as they work here — deactivating that is an office action for someone who's left. `is_available` is the day-to-day toggle a supervisor flips from the assign screen when someone calls in sick or is on leave, so they stop showing up as a candidate for new lead/helper assignments without touching their employment record. It says nothing about tasks they're already on.

**Freelancers see only their own tasks and the sites attached to them** — never the customer list or other technicians' records. A freelancer may work for a competitor next month.

**`photo` and `language` are the two fields a technician can change about their own record, from My profile.** Everything else on this table (role, country, employment type, availability) is an office-side decision made by a supervisor or manager elsewhere. A supervisor/manager with `manage_technicians` can also set someone else's photo from the roster's edit screen; the same permission also gates adding a new record in the first place, from the roster's own "Add technician" screen — country comes from whoever's adding it (or their active country, if a manager), same as customer creation. No `user` is created or linked at that point; the record works standalone until SSO exists to attach one.

**Relocating a technician to another country, from that same edit screen, is manager-only — not just `manage_technicians`.** It's the only other place in the app (besides the header's active-country switcher, also `require_manager`) where one action reaches across a country boundary; letting any supervisor do it would mean one country's supervisor could move a technician into a country they have nothing to do with. A supervisor with `manage_technicians` still sees and uses the same screen, just without the country field.

**Relocating just changes the column — nothing else moves with it.** Any tasks the technician is actively assigned to in their old country stay exactly as they were (still visible and manageable there); relocating doesn't reassign, cancel, or otherwise touch them. A manager still needs to sort those out by hand from the assign screen, same as any other lead change.

**Every screen is country-scoped to the viewer's own `technician.country` — except a manager can switch it.** A "which country am I looking at" value in the session (default: their own), changed from a dropdown in the header, never a written field anywhere — `technician.country` itself never changes when a manager switches. Every country-filtered query in the app (tasks, technicians, customers, tickets, reports) uses this active country, not the manager's home country directly, so switching to Egypt and adding a customer creates it under Egypt, not wherever the manager happens to be based. Supervisors and technicians have no switcher — their active country is always just their own, same as before this existed.

### role_permission
Which role can do what — configurable, not hardcoded. One row per (role, permission) pair.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| role | varchar | technician, supervisor, manager |
| permission | varchar | see below |
| allowed | bool | |

Unique on (role, permission).

**Permissions:** `view_dashboard`, `view_tasks`, `create_tasks`, `assign_tasks`, `view_technicians`, `review_skills`, `manage_tickets`, `manage_technicians` (add technicians, edit their profile photo), `manage_customers` (add customers and sites). Only the supervisor-side actions — the ones that plausibly differ by role. Self-service technician screens (My week, My progress, My skills, task detail, report form) stay open to any signed-in technician regardless of role; there's no case yet for excluding a role from their own record, so they aren't part of this table. Filing a report is also not gated by a separate permission — it's the technician's own action, marking the task completed. Approving it closed is the one exception on this whole screen: it's manager-only, a fixed floor like `role_permissions` itself rather than a row in this table (see task's status-flow note).

**Managed from its own screen (`/tasks/roles/`), manager-only, and deliberately not itself gated by a `role_permission` row.** If "who can manage permissions" were just another row in the table it manages, a bad edit could disable it for every role at once with no way back in short of a database fix. Manager access to that one screen is a fixed floor (`require_manager`), everything else runs through it.

**Seeded to change nothing on its own.** The migration that creates this table reproduces exactly what used to be hardcoded — supervisor and manager allowed, technician not — for every permission. Nothing about who can do what actually changes until a manager edits the matrix.

### technician_skill
The capability matrix. Answers "can he do this job", separately from "will he do it well". Holds only the *current* level — `technician_skill_assessment`, below, keeps the full history behind it.

**There are no certificates, but there is a starting guess — and now it has to be shown, not just claimed.** A technician may self-rate a skill he's never been rated on, but only by uploading a photo or short video of himself actually doing that repair, smoothly and fast; that self-rating is honest evidence, but it never counts toward certification on its own. Only a supervisor's confirmation does — `source` records which kind this row is.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| technician_id | FK → technician | |
| skill_id | FK → skill | |
| level | int | 1–4 |
| source | varchar | `self` or `supervisor` |
| set_by_id | FK → technician | the technician himself for a self-rating, the supervisor for a review |
| set_on | date | |
| note | varchar | nullable |
| evidence | file | nullable — photo/video, required when a technician self-rates, untouched by a supervisor's confirmation |

Unique on (technician_id, skill_id) — a fresh rating overwrites this row; the old value survives in the history table. `evidence` is the one column that doesn't get overwritten by a supervisor's confirmation (`update_or_create`'s `defaults` never mentions it) — the technician's proof stays attached to the row even after it's been reviewed, so it can always be looked at again.

### technician_skill_assessment
Append-only. Every self-rating and every supervisor review that has ever touched a (technician, skill) pair, never edited or deleted. Same columns as `technician_skill` (evidence included) minus the uniqueness constraint. This is what makes a self-rating auditable: nothing is lost when a supervisor overwrites it.

### technician_conduct / technician_conduct_assessment
The same two tables, minus `evidence` — a conduct area (cleanliness, punctuality, ...) isn't a specific repair task, so there's nothing to film — except `skill_id` becomes `conduct_area_id`, pointing at `conduct_area` instead. Kept as separate tables rather than folding conduct areas into `skill`, because merging them would blur what `skill` means everywhere else it's used (`task.required_skill`, the assign screen's candidate levels).

### How a level gets set

**Step one: the technician does the repair and shows it.** The first time a skill has no row yet, its owner can self-rate it using the same four sentences a supervisor will use later — but only alongside a photo or short video of himself actually performing that repair. This is proof to review, not a claim to take on trust. Conduct areas skip the evidence — there's nothing to film for punctuality or cleanliness — and use the same four sentences on their own.

**Step two: the supervisor confirms it against the evidence, not opinion.** For a skill, they watch or view what was uploaded and judge it against the same rubric below. For a conduct area, it's whatever's observable for that area — cleanliness of the last few jobs, whether procedure was followed, and so on.

**1 — Has never done this repair alone / not yet observed.** Helper only.
**2 — Has done it under supervision and it went fine.** Leads simple jobs, calls for help on faults.
**3 — Has led this work alone, repeatedly, with no callbacks.** Send him and do not worry.
**4 — Others call him when they are stuck.** Can train.

Each of these is checkable against what's in the video. "Did he replace that pin cleanly, and did it hold?" has a yes or no answer two supervisors would agree on. "Is he skilled?" does not.

Write this on one page in Arabic and English. Without certificates, these four sentences are the only thing holding the levels together across countries.

**A level never changes by itself.** Nothing computed writes to `technician_skill` or `technician_conduct` — not a solve-rate crossing a threshold, not a self-rating sitting unreviewed for a month. A supervisor looks at the evidence and makes the call, deliberately, every time, in both directions.

**Once there is history, show it before he decides.** The review screen should display the facts first — *led 11 Panatta tasks, 1 came back within 30 days* — so the supervisor judges against the record rather than from memory.

**Level 4 needs two supervisors to agree.** It is the level that gets inflated, because it usually carries pay or status. Requiring a second name costs nothing and keeps the top of the scale meaningful. *(Not yet enforced by the app — a process rule for now.)*

**Recording `set_by_id` is what keeps it honest.** If a level is questioned later you know whose judgement it was, and supervisors are measurably more careful when their name is attached.

**Let the data correct the judgement over time.** After a year, compare first-time fix rates by level. If level 3 technicians on a brand are no better than level 2, either the definitions are wrong or someone is inflating. The event log becomes the check on the opinion.

### The certification bar

**"Certified technician" means level ≥ 3, supervisor-confirmed, on every `other`-category skill and every conduct area.** Cardio skills don't count toward this bar — clearing one instead marks readiness for the supervisor track. Self-ratings don't count either, no matter how high; only a supervisor's confirmation moves the bar.

This status is shown to the technician themselves (My progress) and to supervisors reviewing their country's roster. It's a fact, not a gate: nothing in the app currently blocks a task assignment or pay decision on it. (There used to be a report-approval-rate metric shown alongside it, tied to the old report-review workflow — removed along with that workflow; see §4's task status flow.)

### The 90-day track

**A new technician works as helper, alongside a supervisor as lead, until certified.** The target is to get there within 90 days of `hired_on` — computed, not stored, from real progress against that clock: days elapsed vs. days remaining, and confirmed count vs. total required, shown to the technician on My progress and to their supervisor on the roster.

**`on_track` is one disclosed comparison, not a verdict.** Confirmed share of the bar vs. elapsed share of the 90 days — if a technician is 30 days in and already a third certified, that's on track; less than that, it's flagged. It never blocks anything and it's not itself a metric to optimize; a supervisor still decides what a "behind" flag means for that person.

**No `hired_on` means no 90-day track shown at all** — silently, not as a warning. It's an existing nullable field with real historical gaps; this just gives it a second use once it's filled in.

---

## 4. Tasks

### task
| Column | Type | Notes |
|---|---|---|
| id | PK | |
| task_number | varchar | unique, auto-generated per country, e.g. `AE-0001` |
| site_id | FK → site | **the only certain field at creation** |
| task_type_id | FK → task_type | nullable |
| brand_id | FK → brand | nullable |
| required_skill_id | FK → skill | nullable |
| min_level | int | nullable |
| description | text | what the customer reported |
| priority | varchar | low, normal, high, emergency |
| source | varchar | phone, whatsapp, email, internal, portal |
| is_warranty | bool | nullable until known |
| billing_type | varchar | warranty, contract, chargeable, goodwill |
| reported_at | timestamptz | |
| promised_at | timestamptz | what the customer is owed |
| scheduled_for | timestamptz | nullable — the finalized day and time, only ever set once both are known |
| scheduled_date | date | nullable — a manager's day-only commitment, while `scheduled_for` waits for a supervisor to add the time |
| schedule_time_locked | bool | true once a manager sets day + time together — a supervisor can no longer edit `scheduled_for` directly |
| estimated_hours | decimal | nullable — the supervisor's rough guess, not a computed average |
| status | varchar | see below |
| created_by_id | FK → user | |
| responsible_supervisor_id | FK → technician | nullable — who's accountable for staffing it, not who created it |
| pak_reference_number | varchar | blank — internal only, never emailed to the customer |
| shipping_company | varchar | blank — one of a fixed list (DHL, FedEx, UPS, Aramex, TNT, local courier, other) |
| shipping_tracking_number | varchar | blank — set once parts have shipped |
| quotation | file | nullable — pdf/image, edit screen only, never on create |
| quotation_uploaded_at | timestamptz | nullable — set when the file is uploaded, cleared when it's removed |
| factory_offer | file | nullable — pdf/image, edit screen only |
| factory_offer_uploaded_at | timestamptz | nullable |
| invoice | file | nullable — pdf/image, edit screen only |
| invoice_uploaded_at | timestamptz | nullable |
| delivery_note | file | nullable — pdf/image, edit screen only — the shipment paperwork, uploaded once the parts arrive |
| delivery_note_uploaded_at | timestamptz | nullable |
| schedule_notified_at | timestamptz | nullable — set manually, or automatically when `notification_settings.auto_notify_on_reschedule` is on |
| schedule_notified_by_id | FK → user | nullable |

**Status flow:** `new` → `assigned` → `accepted` → `in_progress` → `completed` → `closed`.
Plus `blocked` and `cancelled` as endings.

**Filing the report marks the task completed; a manager approving it is what actually closes it.** The lead taps accept → en route → arrived → start (each logs a `task_event`; only "accepted" and "started" move `status`), then submits the report from their phone — that submission moves `status` to `completed` and logs a `completed` task_event, in the same transaction as saving the report itself. From there, only a manager can close it: an "Approve and close" button on the task's own detail page (no separate queue screen, no rejection reason — just that one button), which moves `status` to `closed` and logs a `report_approved` task_event. Re-submitting the report at any point afterward (a correction) is always allowed and doesn't move `status` backward or re-fire the completed event — there's nothing to unlock first, only the close itself is gated.

This is deliberately lighter than an earlier version of the same idea, which had a full pending/approved/rejected workflow with a required rejection reason and its own review screen — that got removed for not fitting how the business runs, and this replacement is a narrower requirement (a manager's own sign-off before a task counts as done), not a return to that queue. It's also manager-only, not configurable per role the way most of `role_permission` is — the same fixed-floor pattern `role_permissions` itself uses, so a bad edit to the permission matrix can't accidentally hand this out or lock everyone out of it.

**`estimated_finish` (`scheduled_for` + `estimated_hours`) is computed, not stored.** It only exists when both inputs are known, and it's shown wherever a technician's schedule is — My week, and the supervisor's board for that technician — never persisted as its own column, so there's nothing to keep in sync if either input changes.

`promised_at` and `scheduled_for` are different. The first is the customer's deadline, the second is the slot you planned. A task due Tuesday and a task planned for Tuesday are not the same thing.

**A manager scheduling a task picks one of two modes, every time.** Day and exact time — `scheduled_for` is set directly, `schedule_time_locked` becomes true, and from then on only a manager can change it. Or day only — `scheduled_date` is set, `scheduled_for` stays empty, and any supervisor who can already edit the task can add the exact time themselves (`set_schedule_time`, `tasks/views.py`) without needing anyone's approval; that fills in `scheduled_for` and leaves the task unlocked. A supervisor scheduling their own task from scratch is never affected by any of this — no manager has locked anything yet, so they set `scheduled_for` directly, exactly as before this existed.

**Once locked, a supervisor can't edit `scheduled_for` at all — they file a `schedule_change_request` instead**, proposing a new date and time with an optional reason. A manager reviews it right on the task's own detail page (approve or deny, no separate queue, same pattern as approving a report) — approving applies the requested time and re-locks it, denying leaves the existing schedule untouched. Only one pending request per task at a time. A manager, unlike a supervisor, can always just edit the schedule directly regardless of lock state — the request flow exists only because a supervisor has no other way in once it's locked.

**A manager can also flip `schedule_time_locked` on its own, without touching the date or time at all** — a plain "Lock" / "Open to supervisor" toggle right next to the schedule on task detail (`toggle_schedule_lock`, manager-only), for when the day and time are already right and a manager just wants to hand editing rights back to (or take them away from) whoever's staffing the task. Only meaningful once `scheduled_for` is set — there's nothing to lock before then.

**`quotation`/`factory_offer`/`invoice`/`delivery_note` are the paperwork trail** — a pdf (or a photo of a paper one), uploaded from the task's Edit screen, never at creation. All four optional and independent: a task can have any subset of them at any time, in whatever order the actual paperwork happens to arrive. Each has its own `_uploaded_at`, stamped by `TaskEditForm.save()` when that file actually changes (cleared back to null if the file is removed) — not a general "last edited" timestamp, just that one field. Each also gets its own browsable tab in Django admin (Quotations / Factory offers / Invoices / Delivery notes, proxies over `task` — no separate table), filtered to tasks that actually have that file, with a link back to the task, a date-hierarchy calendar on `_uploaded_at` to browse by when it arrived, and View/Download actions.

**`blocked` is a legitimate outcome** — gym closed, no key, customer absent. It must not count against the technician.

**Any task field can be edited after creation** (except `site` — moving a task to a different site is a different operation, not an edit) from a dedicated edit screen, separate from the assign screen that already handles reassigning the lead/helpers with its own history. Rescheduling — changing `scheduled_for` — logs a `rescheduled` task_event, same as any other status-relevant change.

**`responsible_supervisor` is who owns getting the task staffed — separate from, and set independently of, who's actually assigned to do the work.** Optional at creation, and picked from the same pool `customer_ticket.assigned_to` draws from (any active supervisor or manager in the country, never a technician). It has real teeth: once set, only that supervisor or a manager can edit the task, open its assign screen, or act on it from task detail (notify the customer) — see `role_permission` above for the rest of the access model this sits alongside. Requesting feedback is a separate, narrower restriction on top of this one: manager-only regardless of ownership, see `customer_feedback` below. An unowned task (still the default for anything created before this existed) stays open to whoever the usual permission already let in, and any supervisor can claim it from the edit screen — the same screen a manager uses to reassign an owned one. No history is kept on it, unlike the lead, which the `task_event` log already tracks.

**Telling the customer about a schedule is controlled by one global switch, `notification_settings.auto_notify_on_reschedule`.** Off (the default): a supervisor clicks "Notify customer" from task detail, once `scheduled_for` and the site's `contact_email` are both set, and an email goes out immediately — the deliberate, manual path this started as. On: the same email fires by itself the moment an edit changes `scheduled_for` (still only when a `contact_email` exists). Either way `schedule_notified_at`/`_by` record that it happened and who/what did it, and the manual button becomes "Notify again" for a resend. An edit that doesn't change `scheduled_for` never notifies, in either mode — only a change to the scheduled time counts as a reschedule.

**A delay notice is a different message, always manual.** When the team is running behind — traffic, a previous job overrunning — a supervisor sends a short apology from task detail with a required reason, logged as a `delay_notice` task_event (the reason lives in the event's own `note`, so no extra columns on `task` are needed; a task can have any number of these over time, unlike the single schedule-confirmation email).

**`pak_reference_number`/`shipping_company`/`shipping_tracking_number` work the same way** — see `customer_ticket` above, where all three columns also live and the notify button is explained in full.

**Most shipments come from the factory, not the customer** — so alongside the customer-facing "Notify customer" button, setting or changing `shipping_tracking_number` from the task's Edit screen also always emails the task's own `responsible_supervisor`, automatically, no button to click. This is a different email to a different audience (internal, in English, with a link back to the task) from the customer-facing one, and it only fires when there's a `responsible_supervisor` with a login and an email on file — an unowned task notifies no one.

### notification_settings
A single row (`pk=1`, created on first use), manager-controlled from the same Roles & permissions screen as the permission matrix — not per-country, one switch for the whole app.

| Column | Type | Notes |
|---|---|---|
| id | PK | always `1` |
| auto_notify_on_reschedule | bool | default `false` |

### customer_ticket
A complaint or request — always submitted by a signed-in customer picking one of their own registered sites. There's no anonymous path: a brand-new customer reaches the company outside the app (phone, WhatsApp, email) and staff registers them — `/tasks/tickets/new/`, the old public no-login form, now just redirects to the portal login. `customer_id`/`site_id` are still nullable columns rather than required ones, so any ticket already in the database from before this changed stays valid as-is; every ticket submitted from now on always has both set.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| ticket_number | varchar | unique, auto-generated per country at creation — e.g. `AE-T0001`. Same per-country prefix a task number uses (`country.task_prefix` or the ISO code), marked with a `T` so the two sequences are never confused. Generated by `save_new_ticket` (`tasks/views.py`), the same retry-on-collision pattern `_save_new_task` uses |
| country_id | FK → country | |
| customer_id | FK → customer | set immediately at submission — the logged-in customer it belongs to |
| site_id | FK → site | set immediately at submission — the customer's own pick from their registered sites |
| company_name | varchar | the customer's name, copied from their account at submission (`customer.name`) |
| customer_code | varchar | blank — copied from `customer.code` at submission, if the customer has one on file |
| site_description | varchar | branch name, as the customer describes it |
| site_address | text | the gym's physical address |
| contact_name | varchar | |
| contact_phone | varchar | |
| contact_email | varchar | nullable |
| shipping_address | text | blank — where replacement parts should be delivered, if different from the site itself; pre-filled from `customer.shipping_address` on the portal path, editable either way |
| serial_numbers | text | one affected machine's serial per line |
| description | text | what's wrong with each machine — asked to keep one paragraph per serial |
| notes | text | blank — anything else the customer wants to add |
| submitted_at | timestamptz | |
| status | varchar | new, converted, dismissed, closed |
| assigned_to_id | FK → technician | nullable — who's handling it, a supervisor or manager (never a technician) |
| assigned_at | timestamptz | nullable |
| pak_reference_number | varchar | blank — internal only, never emailed to the customer |
| shipping_company | varchar | blank — one of a fixed list (DHL, FedEx, UPS, Aramex, TNT, local courier, other) |
| shipping_tracking_number | varchar | blank — set once parts have shipped |
| task_id | FK → task | nullable — set once converted |
| reviewed_by_id | FK → user | nullable |
| reviewed_at | timestamptz | nullable |
| dismissal_reason | varchar | nullable |
| close_reason | varchar | nullable |
| token | varchar(43) | unique, auto-generated — see below |

**`token` lets a ticket be checked, and replied to, without staying logged in** — the same unguessable-link pattern `customer_feedback.token` already uses (`secrets.token_urlsafe(32)`, generated in `save()`). It's what the portal's own ticket list links to (`ticket_status`), and doubles as a no-login fallback if a customer loses their session but kept the link.

**Assignment is ownership, not authorization.** `assigned_to` just says who's looking into a ticket — it can be any active supervisor or manager in the ticket's country (never a technician; tickets stay supervisor-side work, unlike tasks), set by anyone with `manage_tickets`. It doesn't grant the assignee the ability to convert or dismiss; they can open the ticket read-only (so they can see what they've been asked to check), but that decision still requires `manage_tickets` regardless of who it's assigned to. There's no technician-facing "My tickets" screen — a technician's work always shows up as a task once a ticket is converted, tracked the same way as everything else on My week.

**`site_id` is the customer's own pick from their registered sites** — authoritative, not a guess, so it carries straight through to the resulting task with no re-matching and no chance to swap it out mid-conversion; only an admin can change it on the create-task screen. The supervisor reviewing a ticket either converts it (that site, a different existing one they pick instead, or a new one — task creation's own choice), dismisses it with a reason, or closes it with a reason.

**Closed is separate from dismissed.** Dismissed means invalid or spam — never real work. Closed means the issue was genuinely resolved without ever needing a task — advice given over the phone, handled some other way. Both are terminal and end the reply conversation (see `ticket_reply` below); the distinction is only which reason field explains why no task exists.

**Converting reuses task creation itself**, not a separate form — the ticket's free-text fields become initial hints on the normal create-task screen, `task.source` gets set to `portal`, and the ticket links to whatever task comes out of it. Nothing new to keep in sync if task creation changes later. If `pak_reference_number`/`shipping_company`/`shipping_tracking_number` were already set on the ticket, they carry over onto the new task; any of the three can also just be set directly, since parts more often ship after the task exists.

**`pak_reference_number`, `shipping_company`, and `shipping_tracking_number` exist on both `customer_ticket` and `task`, always optional, filled in later once parts actually ship** — never known at submission time. `shipping_company` is a fixed choice (DHL, FedEx, UPS, Aramex, TNT, local courier, other) — free text drifts the same way an unmanaged `task_type` would. The carrier and tracking number are the only two ever emailed to a customer (a manual "Notify customer" button next to each, same fail-silent pattern as the schedule/delay notices) — the carrier is dropped from the email when it's blank, and just the tracking number goes out on its own; PAK is internal bookkeeping and never leaves the app.

### ticket_reply
The back-and-forth on a ticket, either side, in order.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| ticket_id | FK → customer_ticket | |
| sender | varchar | staff, customer |
| sent_by_id | FK → user | nullable — always set for staff; set for a customer only if they replied through their portal login rather than the anonymous token page |
| message | text | |
| attachment | file | nullable — photo, short video, or PDF, same allowlist/25 MB limit as `customer_ticket_attachment` |
| is_quotation | bool | default `false` — staff-only; marks this reply as the quotation, see below |
| sent_at | timestamptz | |

**`is_quotation` sends a dedicated "Your quotation is ready" email with the attachment itself attached, not just linked** — instead of the plain "New reply on your report" notice every other staff reply sends. Requires an attachment (`TicketReplyForm.clean()`); never set on a customer's own reply (`ticket_status` view never reads it off that path, so a crafted POST there can't mark one), and never rendered on the customer-facing reply form to begin with.

**Open while the ticket is still new, or converted but the resulting task isn't finished yet** (`_ticket_is_open`, `tasks/views.py`) — closed once dismissed, once the ticket itself is closed directly, or once that task itself reaches `closed`. Not tied to `customer_ticket.status` alone: "converted" can mean the work just started, and the customer should still be able to ask about it. A **cancelled** task does *not* close the conversation — cancellation isn't a resolution, so the issue is still open and staff still needs to be able to talk to the customer about it. Once actually closed, the thread becomes read-only on both the staff review screen and the customer's own token page. A customer can reply from either place they can already reach a ticket: the public `ticket_status` page (token-based, no login) or, if they're logged in, the same page linked from their portal — there's no separate reply screen to keep in sync.

**Each reply sends a best-effort email the other way**, same fail-silent pattern as every other notification here. A staff reply emails `customer_ticket.contact_email`, if one was given. A customer reply emails `assigned_to`'s login email, if the ticket is assigned to someone with one on file — there's no fixed office address to fall back to, so an unassigned ticket's customer replies simply don't email anyone until someone picks it up.

### ticket_internal_note
A manager-tier-only progress note on a ticket — separate from `ticket_reply`, which the customer (and, for triage, any staff with `manage_tickets`) can see. This one is never shown to the customer, and never shown to a supervisor or technician either — only Manager and Admin, gated on `technician.is_manager_tier` rather than the `manage_tickets` permission, since that permission is often also granted to supervisors for day-to-day ticket triage.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| ticket_id | FK → customer_ticket | |
| author_id | FK → user | nullable |
| message | text | |
| created_at | timestamptz | |

**A running log, not a single overwritable field** — each note is its own row, timestamped and attributed, so a manager can see how the state of a ticket evolved over time rather than just its latest value.

### customer_ticket_attachment
A customer's own phone photo or short video of the fault, uploaded with the ticket. No `uploaded_by` of its own — the ticket already records who submitted it (`ticket.customer`) — and no link/URL option the way `task_attachment` has, since a customer only ever uploads a real file. Capped at 10 files per ticket, on top of the 25 MB-per-file limit, so a single submission can't attach an unbounded number of files and exhaust storage.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| ticket_id | FK → customer_ticket | |
| file | file | image or short video, 25 MB limit |
| uploaded_at | timestamptz | |

**Still shown once the ticket becomes a task.** Converting a ticket doesn't copy these rows into `task_attachment` — the file stays owned by the ticket, exactly where it was uploaded — but the task's own detail page reads them straight off `task.ticket.attachments` (the OneToOne back to `customer_ticket`) and shows them in their own "Attachments from the customer's ticket" section, so a supervisor sees the original evidence without anyone re-uploading it.

### task_asset
Which machines the task covers. Populated by the technician during the work, not at creation.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| task_id | FK → task | |
| asset_id | FK → asset | |
| outcome | varchar | repaired, replaced, not_repairable, inspected_ok |

Many-to-many because a customer reporting three broken treadmills is one visit, and an installation is one visit covering twenty machines. A single `asset_id` on the task would force you to split those artificially.

### task_product
The delivery note's contents, structured — for installation and loading tasks, where stock is delivered ahead of or alongside the visit rather than consumed during it (that's `part_used`, on the work report instead, see §5).

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| task_id | FK → task | |
| product_code | varchar | required |
| serial_number | varchar | blank — delivery notes are usually just model codes and quantities, per `asset`'s own note above; a serial is a bonus when the paperwork actually has one |
| quantity | int | default 1 |

**Entered from the task's Edit screen, replaced wholesale on every save** — not an append-only log the way `task_event` is. The same information already exists as a document (`task.delivery_note`); this is the same content kept structured, so it can be searched and listed rather than only read off a scanned PDF. A row needs at least a `product_code` to save; a blank row is silently dropped, and quantity defaults to 1 when left empty.

### task_attachment
Photos, videos and links. The supervisor attaches the customer's evidence at creation, before any report exists.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| task_id | FK → task | |
| storage_kind | varchar | `file` or `link` |
| url | varchar | |
| media_type | varchar | photo, video, document |
| purpose | varchar | fault, serial_plate, before, after (technician's own fault evidence — `TaskAttachmentUploadForm`), or delivery_note, written_report, other (a supervisor/manager/admin document — `StaffAttachmentUploadForm`) |
| source | varchar | customer, supervisor, technician |
| uploaded_by_id | FK → user | |
| uploaded_at | timestamptz | |

Compress photos on upload and never autoplay video. Technicians are on mobile data.

**Two upload forms share this one table, each scoped to its own `purpose` values.** A technician adds fault-evidence photos/video from their own task page (`my_task_detail`); a supervisor, manager, or admin — never a technician — adds documents (a delivery note, a written report, anything else, PDF included) from the staff task detail page, a repeatable list separate from the fixed one-per-task `quotation`/`factory_offer`/`invoice`/`delivery_note` file fields on `task` itself. Staff uploads are recorded with `source = supervisor` regardless of which of the three roles actually uploaded — `uploaded_by` already has the exact user if that's ever needed.

### task_assignment
Handles several technicians on one task, and one technician across many tasks.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| task_id | FK → task | |
| technician_id | FK → technician | |
| role | varchar | `lead` or `helper` |
| assigned_at | timestamptz | |
| is_active | bool | false once replaced |
| ended_at | timestamptz | nullable |
| end_reason | varchar | required on reassignment — one tap, never free text |

**Exactly one active `lead` per task.** Enforce in the database.

**Reassignment replaces the row, never edits it.** Set `is_active = false`, create a new row. Overwriting erases who held the task, and since accountability follows the lead, the wrong person would carry the outcome.

**Reason codes:** sick, leave, overloaded, skill_mismatch, customer_request, emergency, vehicle, other. Include `other` so nobody has to stop and think.

**Blocked once work starts.** After `in_progress`, handover means closing the task and raising a new one — otherwise two people's work lands in one report.

**Notify the technician when a task is taken away**, not only when one is given.

### task_event
**The most important table.** Written automatically by the app, never typed. Every reliability number later comes from here.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| task_id | FK → task | |
| event_type | varchar | see below |
| occurred_at | timestamptz | UTC |
| actor_id | FK → user | |
| corrected_at | timestamptz | nullable — supervisor's correction |
| corrected_by_id | FK → user | nullable — supervisors only |
| note | text | |

**Event types:** created, assigned, reassigned, rescheduled, delay_notice, schedule_change_requested, schedule_change_approved, schedule_change_denied, accepted, en_route, arrived, blocked, started, paused, resumed, completed, report_submitted, report_rejected, report_approved, closed, reopened, cancelled, negligence. `completed` fires when the lead files the report, `report_approved` when a manager approves it through the normal pipeline (see §4/§5). `closed` is the separate manager-only direct-close bypass — for a task that turns out not to need a report at all (customer cancelled, resolved another way) — usable from any status except already-`closed`; the reason lives in the event's own `note`. Both `report_approved` and `closed` land the task on `Task.Status.CLOSED`, just by different paths. `report_rejected` and `reopened` are the ones still unused: this round of approval has no reject step, just a single approve action, and nothing yet reopens a closed task.

**`paused`/`resumed` cover a multi-day task** — the lead marks themselves stopped for the day (`note` optional — any handoff context) and started again the next morning, any number of times, without moving `task.status` off `in_progress` at all; a task's paused/resumed history is just more taps on the same table, not a status of its own. While paused, blocking and filing the report are both refused (`_task_is_paused`, `tasks/views.py`) — filing while paused would prematurely mark it finished, and blocking doesn't make sense on work that's already stopped. Worked time is meant to come free from this later, the same way travel and on-the-tools time already do below: `started` → first `paused`, each `resumed` → next `paused`, and the last `resumed` → `completed`, summed.

**`negligence` is the reliability signal itself** — a manager, reviewing a customer complaint about a specific past visit, marks that already-`closed` task as the technician's own fault rather than bad luck (`actor` is the manager who flagged it, not the technician; `note` explains what went wrong). It's attributed to whoever was the task's active lead at read time — not stored per-technician on the event, since the lead assignment already carries that. Never shown to the flagged technician (excluded from both `task_detail` and `my_task_detail`'s event list unless the viewer is a manager) — it's a manager-only reliability record, surfaced on the technician's skills/review screen (`tasks:technician_skills`) alongside their skill and conduct ratings.

The technician taps buttons; he never types a time. If this table is skipped, you will have a year of operations and still no way to answer who you can depend on.

**Task duration comes free from these taps.** `started` to `completed` is time on the machine, minus any paused/resumed gaps (see above); `en_route` to `arrived` is travel. `completed` to `closed` is the manager's own approval lag, not technician time — keep those two apart in any duration reporting. Nothing extra to record.

Use duration for **scheduling** — once you know a cable replacement takes about ninety minutes, the week view becomes real instead of optimistic — and for **spotting outliers**, where a four-hour task among one-hour ones usually means something went wrong that nobody reported.

**Never judge a technician on speed.** Forty minutes with the machine back in two weeks is worse than two hours that holds. If technicians work out that speed is measured, they will rush, and the first-time fix rate will quietly fall.

### schedule_change_request
A supervisor's ask to move a locked schedule — see `task.schedule_time_locked` above for when this is the only path in.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| task_id | FK → task | |
| requested_by_id | FK → technician | |
| requested_scheduled_for | timestamptz | the proposed replacement for `task.scheduled_for` |
| reason | varchar | blank |
| status | varchar | pending, approved, denied |
| reviewed_by_id | FK → user | nullable |
| reviewed_at | timestamptz | nullable |
| review_note | varchar | blank — a manager's note either way |
| created_at | timestamptz | |

At most one `pending` row per task at a time — a second request can't be filed until the first is resolved. Approving copies `requested_scheduled_for` onto the task (re-locking it, same as a manager setting it directly) and logs both a `rescheduled` and a `schedule_change_approved` task_event; denying touches nothing on the task itself, just the request row, and logs `schedule_change_denied`.

### Correcting a forgotten tap

Technicians forget to press complete and remember in the car. **Only a supervisor may correct a time.** The technician can flag that a time is wrong and state what it should have been; he cannot change it himself.

- **The supervisor corrects it at report review**, which he is doing anyway.
- **Never before `arrived`, never in the future.** Enforce both.
- **The original is kept.** `occurred_at` is never overwritten; the correction goes in `corrected_at` with the supervisor's id in `corrected_by_id`.

**Prevention beats correction.** If a task has been `in_progress` for several hours with no activity, the app should ask — *still working at Fitness Time Olaya?* — one tap to confirm or complete. This catches most forgotten buttons at the time, while the answer is still accurate, and keeps corrections rare enough that reviewing them is not a burden.

**Watch the pattern, not the case.** One correction is a forgotten button. A technician whose times need correcting on most tasks is a habit worth asking about.

**The bigger risk is not times.** Marking a task resolved when it is not costs you a second visit and a customer's patience. The 30-day callback check catches that regardless of what times were recorded.

---

## 5. Reports

### work_report
| Column | Type | Notes |
|---|---|---|
| id | PK | |
| task_id | FK → task | one per task |
| findings | text | what the technician found |
| action_taken | text | |
| resolved | bool | |
| labour_hours | decimal | |
| customer_name | varchar | who signed |
| signature_url | varchar | |
| submitted_at | timestamptz | |

**The lead submits one report for the whole task**, with helpers listed. The customer signs once.

**No `approved_at`/`rejection_reason` columns here, and reports are never locked.** The task's own `status` carries the approval state now (`completed` → `closed`, see task's status-flow note above), not the report — a manager approving is a task action, logged as a `report_approved` task_event, not a field written on this row. The report itself can always be corrected by submitting again, whatever the task's status is; nothing about it needs unlocking first.

### part_used
| Column | Type | Notes |
|---|---|---|
| id | PK | |
| report_id | FK → work_report | |
| part_code | varchar | |
| description | varchar | |
| quantity | int | |
| unit_cost | decimal | |
| currency_code | char(3) | never store an amount without its currency |

### customer_feedback
A rating request sent to the customer once their task is closed (report filed). Not automatic — a supervisor sends it deliberately, from the task's own detail page.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| task_id | FK → task | one per task |
| token | varchar | random, unique — the public link's only credential |
| requested_at | timestamptz | updated on every resend |
| requested_by_id | FK → user | which supervisor sent it |
| rating | int | nullable — 1–5, until the customer answers |
| comment | text | nullable |
| submitted_at | timestamptz | nullable |

**The customer is never a user of this system.** The link is the only thing standing in for a login — reached at `/reports/feedback/<token>/`, no authentication, no supervisor-facing chrome. Requires a `contact_email` on the site; there's no fallback channel yet if one isn't on file.

**Sending is manual, every time, and manager-only.** No automatic email fires on close — a manager decides per task whether asking makes sense, and can resend the same link (it doesn't expire or rotate) if the customer never answered. A fixed floor like `approve_report`/`close_directly`, not a `role_permission` row — a supervisor, even one who owns the task, can't send it.

---

## 6. Measuring reliability

Capability is the skill matrix — a supervisor's judgement about what a technician can do. Reliability is different: it is computed from `task_event`, and nobody types it.

### What you can measure

| Metric | How | Available |
|---|---|---|
| On-time arrival | `arrived` before `promised_at`, excluding `blocked` | Immediately |
| Acceptance latency | Median minutes from `assigned` to `accepted` | Immediately |
| Tasks led vs helped | Count by `role` | Immediately |
| First-time fix rate | Completed tasks with no new task on the same asset within 30 days | Around month nine |

**First-time fix rate is the best of these and the last to arrive.** It needs the asset register, and assets only come into existence as machines get serviced. Do not promise this number to anyone in year one.

### Roll it out in three stages

**Months 1–3: measure nothing, show nothing.** Collect events only. Any figure computed on a few weeks of data is noise, and showing noise once destroys trust in the system permanently.

**Months 4–8: show facts, not scores.** Tasks completed, tasks helped on, on-time percentage, median acceptance latency — each hidden individually until its own sample clears the 20-task floor below, broken down by brand. Plain numbers a technician can check and argue with, no ranking, no single combined score. **Built** — `_reliability_stats` (`tasks/views.py`), shown on My progress and the supervisor's technician-skills review screen.

**Month 9 onward: add first-time fix, broken down by brand.** This is when the original question — who can I depend on — becomes genuinely answerable. **Not yet built** — still needs the asset register to mature with real volume, not just a query; nothing here promises it early.

**Exception, made deliberately: the certification bar and country leaderboard (§3) are visible from day one.** Unlike first-time fix or on-time %, they aren't inferred rates that need a sample size to mean anything — they're a direct count of supervisor-confirmed levels, which exists the moment a supervisor confirms one.

### Four rules

- **Only the lead's record is affected** by a task outcome. Helpers get participation counts.
- **Break down by brand.** Strong on Panatta often means weak on Digilock, and one combined number hides exactly what you need to see.
- **Compare within country only.** Task density, traffic and site access differ too much between markets.
- **Hide anything below 20 completed tasks.** A score built on four tasks is noise.

### Do not connect this to pay in year one

Once money depends on a number, people optimise the number instead of the work — closing tasks early, avoiding difficult jobs, disputing every blocked task. Use it for training and assignment decisions first. If it still looks fair after a year of that, then consider rewards.

---

## 7. Indexes

```sql
CREATE INDEX ON task (status, promised_at);
CREATE INDEX ON task (site_id, status);
CREATE INDEX ON task (scheduled_for, status);
CREATE INDEX ON task_event (task_id, occurred_at);
CREATE INDEX ON task_event (event_type, occurred_at);
CREATE INDEX ON task_assignment (technician_id, is_active);
CREATE INDEX ON task_asset (asset_id);
CREATE INDEX ON technician_skill (skill_id, level);
CREATE INDEX ON technician_skill (source);
CREATE INDEX ON technician_conduct (source);
CREATE INDEX ON asset (site_id, status);
```

---

## 8. Not in this release

Each is a real need eventually. None belongs in the first version.

- Warranty claim submission workflow — capture `is_warranty`, `labour_hours` and photos now; claim from an export until the volume justifies a module
- Reward points — see the warning in section 6; do not tie metrics to pay in year one
- Parts inventory and stock
- Invoicing — accounting handles it; `billing_type` gives them a clean export
- Preventive maintenance schedules
- Technician leave calendar
- Route optimisation
- Offline mode

---

## 9. Build order

1. `country`, `brand`, `skill`, `task_type` — reference data. Seed `task_type` from last month's real work, in Arabic and English.
2. `customer`, `site` — active customers only. No WhatsApp history.
3. `technician`, `technician_skill`.
4. `task`, `task_assignment`, `task_event`, `task_attachment` — the core.
5. `work_report`, `part_used`, `asset`, `task_asset` — the technician's phone screen, where assets get created.
6. Metrics — only after three months of real event data exists.

---

## 10. The screens

**Supervisor (web):** dashboard, task list and week view, create task, edit task, assign, technician roster, add a technician, a technician's board, review a technician's skills, edit a technician's photo, customers list, add a customer, a customer's sites (add one), tickets list, review a ticket. Filing/viewing a task's report happens right on that task's own detail page — there's no separate reports queue.

**Manager (web):** roles & permissions, skills list, add a skill, requesting customer feedback — everything else a manager sees is whatever the matrix currently grants a manager, which starts out as everything on the supervisor list above.

**Technician (phone):** my week, task detail with photos, report form, my progress, my skills, my profile (own photo, language, phone, email, password).

**Customer (public, no login):** the feedback form — reached only through the emailed link, never linked from anywhere inside the app; and a ticket's own status page, reached by its token, which doubles as a no-login fallback for checking on or replying to a ticket without staying signed in. Submitting a new ticket itself always requires the portal login below — the old public ticket form now just redirects there.

**Customer (portal, login required):** their own account, created by staff from the customer's edit screen — a home page listing every ticket they've submitted and a summary-only service history, and a form to report a new problem at one of their own sites (the company/site/address are never typed here at all, since a login already knows all of that).

**The dashboard is a summary, not a new source of truth.** It shows who's available and every open task's lead and schedule at a glance — country-scoped, same as the roster and week view — but nothing lives only there; task list and week view remain the detailed screens for actually managing that work.

Twenty-two screens plus a handful of public/token pages (feedback form, ticket status). That is the whole application.
