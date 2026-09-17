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
**One skill per brand, unless the brand itself sells more than one line.**

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| brand_id | FK → brand | |
| name | varchar | |
| category | varchar | `other` or `cardio` — see below |
| is_active | bool | |

A finer split — mechanical versus electronic, strength versus cardio — is tempting but wrong at the start, and stays wrong until someone can name one technician who is good at a category and another who is not. Most brands never clear that bar and stay a single row.

**Panatta and Skillcore did clear it.** Both sell a cardio line alongside their other equipment, and the business needs to tell them apart: being a certified technician requires competence on every *non-cardio* line, while cardio competence is reserved as the marker of readiness for the supervisor track (see "How a supervisor sets a level," below). So each gets two skill rows — its original line (`category = other`) and a second `Cardio` row (`category = cardio`) — instead of the one row every other brand keeps.

**Splitting later is easy; merging later is not.** Only split a brand when there's a real reason two lines need different ratings, the way cardio does here. Start with skills nobody can tell apart and you have columns of bad data with no way to tell which parts were right.

**Maintenance burden decides whether this survives.** A handful of rows per technician get updated regularly. Dozens do not, and a stale matrix is worse than none because people trust it.

**Let the data find further splits.** After a year, look at callback rates per technician broken down by machine category. If someone's record on a brand is clean for one line and poor for another, that is evidence for splitting that brand too — and it tells you exactly where the line goes, instead of guessing now.

### conduct_area
The non-technical half of the certification bar — cleanliness, procedure adherence, and the other things that make the difference between a technician and a professional. Not tied to any brand.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| name | varchar | |
| name_ar | varchar | |
| is_active | bool | |

Seeded with five areas: cleanliness & site care, professional appearance & conduct, rule & procedure adherence, punctuality & communication, tool & vehicle care. Rated the same way, on the same 1–4 scale, as a skill — see `technician_conduct` below.

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
| segment | varchar | gym, hotel, club, other |
| is_active | bool | |

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

**`photo` and `language` are the two fields a technician can change about their own record, from My profile.** Everything else on this table (role, country, employment type, availability) is an office-side decision made by a supervisor or manager elsewhere. A supervisor/manager with `manage_technicians` can also set someone else's photo from the roster — the same field, two different doors into it.

### role_permission
Which role can do what — configurable, not hardcoded. One row per (role, permission) pair.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| role | varchar | technician, supervisor, manager |
| permission | varchar | see below |
| allowed | bool | |

Unique on (role, permission).

**Permissions:** `view_dashboard`, `view_tasks`, `create_tasks`, `assign_tasks`, `view_technicians`, `review_skills`, `review_reports`, `manage_tickets`, `manage_technicians` (edit a technician's profile photo). Only the supervisor-side actions — the ones that plausibly differ by role. Self-service technician screens (My week, My progress, My skills, task detail, report form) stay open to any signed-in technician regardless of role; there's no case yet for excluding a role from their own record, so they aren't part of this table.

**Managed from its own screen (`/tasks/roles/`), manager-only, and deliberately not itself gated by a `role_permission` row.** If "who can manage permissions" were just another row in the table it manages, a bad edit could disable it for every role at once with no way back in short of a database fix. Manager access to that one screen is a fixed floor (`require_manager`), everything else runs through it.

**Seeded to change nothing on its own.** The migration that creates this table reproduces exactly what used to be hardcoded — supervisor and manager allowed, technician not — for every permission. Nothing about who can do what actually changes until a manager edits the matrix.

### technician_skill
The capability matrix. Answers "can he do this job", separately from "will he do it well". Holds only the *current* level — `technician_skill_assessment`, below, keeps the full history behind it.

**There are no certificates, but there is a starting guess.** A technician may self-rate a skill he's never been rated on; that self-rating is honest, but it never counts toward certification on its own. Only a supervisor's confirmation does — `source` records which kind this row is.

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

Unique on (technician_id, skill_id) — a fresh rating overwrites this row; the old value survives in the history table.

### technician_skill_assessment
Append-only. Every self-rating and every supervisor review that has ever touched a (technician, skill) pair, never edited or deleted. Same columns as `technician_skill` minus the uniqueness constraint. This is what makes a self-rating auditable: nothing is lost when a supervisor overwrites it.

### technician_conduct / technician_conduct_assessment
The same two tables, same columns, same self-then-supervisor flow — except `skill_id` becomes `conduct_area_id`, pointing at `conduct_area` instead. Kept as separate tables rather than folding conduct areas into `skill`, because a conduct area isn't tied to a brand and isn't something a task ever requires — merging them would blur what `skill` means everywhere else it's used (`task.required_skill`, the assign screen's candidate levels).

### How a level gets set

**Step one: the technician guesses.** The first time a skill or conduct area has no row yet, its owner can self-rate it using the same four sentences a supervisor will use later. This is a starting point for the conversation, not a claim.

**Step two: the supervisor confirms it against evidence, not opinion.** For a skill, the question is factual: *how many times has this man led this brand's work alone, and did it come back?* For a conduct area, it's whatever's observable for that area — cleanliness of the last few jobs, whether procedure was followed, and so on.

**1 — Has never led this brand alone / not yet observed.** Helper only.
**2 — Has done it under supervision and it went fine.** Leads simple jobs, calls for help on faults.
**3 — Has led this work alone, repeatedly, with no callbacks.** Send him and do not worry.
**4 — Others call him when they are stuck.** Can train.

Each of these is checkable. "Has he led a Panatta job alone?" has a yes or no answer two supervisors would agree on. "Is he skilled?" does not.

Write this on one page in Arabic and English. Without certificates, these four sentences are the only thing holding the levels together across countries.

**A level never changes by itself.** Nothing computed writes to `technician_skill` or `technician_conduct` — not a solve-rate crossing a threshold, not a self-rating sitting unreviewed for a month. A supervisor looks at the evidence and makes the call, deliberately, every time, in both directions.

**Once there is history, show it before he decides.** The review screen should display the facts first — *led 11 Panatta tasks, 1 came back within 30 days* — so the supervisor judges against the record rather than from memory.

**Level 4 needs two supervisors to agree.** It is the level that gets inflated, because it usually carries pay or status. Requiring a second name costs nothing and keeps the top of the scale meaningful. *(Not yet enforced by the app — a process rule for now.)*

**Recording `set_by_id` is what keeps it honest.** If a level is questioned later you know whose judgement it was, and supervisors are measurably more careful when their name is attached.

**Let the data correct the judgement over time.** After a year, compare first-time fix rates by level. If level 3 technicians on a brand are no better than level 2, either the definitions are wrong or someone is inflating. The event log becomes the check on the opinion.

### The certification bar

**"Certified technician" means level ≥ 3, supervisor-confirmed, on every `other`-category skill and every conduct area.** Cardio skills don't count toward this bar — clearing one instead marks readiness for the supervisor track. Self-ratings don't count either, no matter how high; only a supervisor's confirmation moves the bar.

This status, plus each technician's report approval rate (approved ÷ submitted — see §6), is shown to the technician themselves (My progress) and to supervisors reviewing their country's roster. It's a fact, not a gate: nothing in the app currently blocks a task assignment or pay decision on it.

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
| scheduled_for | timestamptz | nullable — the day the supervisor planned |
| estimated_hours | decimal | nullable — the supervisor's rough guess, not a computed average |
| status | varchar | see below |
| created_by_id | FK → user | |
| schedule_notified_at | timestamptz | nullable — set manually, or automatically when `notification_settings.auto_notify_on_reschedule` is on |
| schedule_notified_by_id | FK → user | nullable |

**Status flow:** `new` → `assigned` → `accepted` → `in_progress` → `completed` → `closed`.
Plus `blocked` and `cancelled` as endings.

**`estimated_finish` (`scheduled_for` + `estimated_hours`) is computed, not stored.** It only exists when both inputs are known, and it's shown wherever a technician's schedule is — My week, and the supervisor's board for that technician — never persisted as its own column, so there's nothing to keep in sync if either input changes.

`promised_at` and `scheduled_for` are different. The first is the customer's deadline, the second is the slot you planned. A task due Tuesday and a task planned for Tuesday are not the same thing.

**`blocked` is a legitimate outcome** — gym closed, no key, customer absent. It must not count against the technician.

**Any task field can be edited after creation** (except `site` — moving a task to a different site is a different operation, not an edit) from a dedicated edit screen, separate from the assign screen that already handles reassigning the lead/helpers with its own history. Rescheduling — changing `scheduled_for` — logs a `rescheduled` task_event, same as any other status-relevant change.

**Telling the customer about a schedule is controlled by one global switch, `notification_settings.auto_notify_on_reschedule`.** Off (the default): a supervisor clicks "Notify customer" from task detail, once `scheduled_for` and the site's `contact_email` are both set, and an email goes out immediately — the deliberate, manual path this started as. On: the same email fires by itself the moment an edit changes `scheduled_for` (still only when a `contact_email` exists). Either way `schedule_notified_at`/`_by` record that it happened and who/what did it, and the manual button becomes "Notify again" for a resend. An edit that doesn't change `scheduled_for` never notifies, in either mode — only a change to the scheduled time counts as a reschedule.

**A delay notice is a different message, always manual.** When the team is running behind — traffic, a previous job overrunning — a supervisor sends a short apology from task detail with a required reason, logged as a `delay_notice` task_event (the reason lives in the event's own `note`, so no extra columns on `task` are needed; a task can have any number of these over time, unlike the single schedule-confirmation email).

### notification_settings
A single row (`pk=1`, created on first use), manager-controlled from the same Roles & permissions screen as the permission matrix — not per-country, one switch for the whole app.

| Column | Type | Notes |
|---|---|---|
| id | PK | always `1` |
| auto_notify_on_reschedule | bool | default `false` |

### customer_ticket
A complaint or request submitted directly by a customer, no login — public, self-identified, not yet linked to a real site. The piece of "customer portal" that turned out to be needed now; the rest of it stays deferred (§8).

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| country_id | FK → country | |
| company_name | varchar | as the customer typed it — not matched to `customer` yet |
| site_description | varchar | branch name or address, as the customer describes it |
| contact_name | varchar | |
| contact_phone | varchar | |
| contact_email | varchar | nullable |
| description | text | what the customer reported |
| submitted_at | timestamptz | |
| status | varchar | new, converted, dismissed |
| assigned_to_id | FK → technician | nullable — who's handling it, a technician or a supervisor |
| assigned_at | timestamptz | nullable |
| task_id | FK → task | nullable — set once converted |
| reviewed_by_id | FK → user | nullable |
| reviewed_at | timestamptz | nullable |
| dismissal_reason | varchar | nullable |

**Assignment is ownership, not authorization.** `assigned_to` just says who's looking into a ticket — it can be any active technician or supervisor in the ticket's country, set by anyone with `manage_tickets`. It doesn't grant the assignee the ability to convert or dismiss; they can open the ticket read-only (so they can see what they've been asked to check), but that decision still requires `manage_tickets` regardless of who it's assigned to.

**Matching is manual, on purpose.** `company_name` and `site_description` are exactly what the customer typed — never auto-matched against `customer`/`site`, because a fuzzy match that's wrong silently attaches a real complaint to the wrong company's history. A supervisor reviews each ticket and either converts it (picking an existing site or creating a new one, the same choice task creation always offers) or dismisses it with a reason.

**Converting reuses task creation itself**, not a separate form — the ticket's free-text fields become initial hints on the normal create-task screen, `task.source` gets set to `portal`, and the ticket links to whatever task comes out of it. Nothing new to keep in sync if task creation changes later.

### task_asset
Which machines the task covers. Populated by the technician during the work, not at creation.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| task_id | FK → task | |
| asset_id | FK → asset | |
| outcome | varchar | repaired, replaced, not_repairable, inspected_ok |

Many-to-many because a customer reporting three broken treadmills is one visit, and an installation is one visit covering twenty machines. A single `asset_id` on the task would force you to split those artificially.

### task_attachment
Photos, videos and links. The supervisor attaches the customer's evidence at creation, before any report exists.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| task_id | FK → task | |
| storage_kind | varchar | `file` or `link` |
| url | varchar | |
| media_type | varchar | photo, video, document |
| purpose | varchar | fault, serial_plate, before, after |
| source | varchar | customer, supervisor, technician |
| uploaded_by_id | FK → user | |
| uploaded_at | timestamptz | |

Compress photos on upload and never autoplay video. Technicians are on mobile data.

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

**Event types:** created, assigned, reassigned, rescheduled, delay_notice, accepted, en_route, arrived, blocked, started, completed, report_submitted, report_rejected, report_approved, closed, reopened, cancelled.

The technician taps buttons; he never types a time. If this table is skipped, you will have a year of operations and still no way to answer who you can depend on.

**Task duration comes free from these taps.** `started` to `completed` is time on the machine; `en_route` to `arrived` is travel. Nothing extra to record.

Use duration for **scheduling** — once you know a cable replacement takes about ninety minutes, the week view becomes real instead of optimistic — and for **spotting outliers**, where a four-hour task among one-hour ones usually means something went wrong that nobody reported.

**Never judge a technician on speed.** Forty minutes with the machine back in two weeks is worse than two hours that holds. If technicians work out that speed is measured, they will rush, and the first-time fix rate will quietly fall.

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
| approved_at | timestamptz | nullable |
| rejection_reason | text | nullable |

**The lead submits one report for the whole task**, with helpers listed. The customer signs once.

**Office review is not bureaucracy.** It is where a warranty claim is saved or lost. If the serial photo is missing or the fault description is too vague for the factory, it goes back the same day while the technician still remembers the machine.

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
A rating request sent to the customer once their report is approved. Not automatic — a supervisor sends it deliberately, from the same screen where they approved the report.

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

**Sending is manual, every time.** No automatic email fires on approval — a supervisor decides per task whether asking makes sense, and can resend the same link (it doesn't expire or rotate) if the customer never answered.

---

## 6. Measuring reliability

Capability is the skill matrix — a supervisor's judgement about what a technician can do. Reliability is different: it is computed from `task_event`, and nobody types it.

### What you can measure

| Metric | How | Available |
|---|---|---|
| On-time arrival | `arrived` before `promised_at`, excluding `blocked` | Immediately |
| Acceptance latency | Median minutes from `assigned` to `accepted` | Immediately |
| Report rejection rate / approval rate | rejected ÷ submitted, or its inverse | Immediately |
| Tasks led vs helped | Count by `role` | Immediately |
| First-time fix rate | Completed tasks with no new task on the same asset within 30 days | Around month nine |

**First-time fix rate is the best of these and the last to arrive.** It needs the asset register, and assets only come into existence as machines get serviced. Do not promise this number to anyone in year one.

### Roll it out in three stages

**Months 1–3: measure nothing, show nothing.** Collect events only. Any figure computed on a few weeks of data is noise, and showing noise once destroys trust in the system permanently.

**Months 4–8: show facts, not scores.** Tasks completed, on-time percentage, rejection rate. Plain numbers a technician can check and argue with. No ranking, no single combined score.

**Month 9 onward: add first-time fix, broken down by brand.** This is when the original question — who can I depend on — becomes genuinely answerable.

**Exception, made deliberately: the certification bar and country leaderboard (§3) are visible from day one.** Unlike first-time fix or on-time %, they aren't inferred rates that need a sample size to mean anything — they're a direct count of supervisor-confirmed levels, which exists the moment a supervisor confirms one. The report approval rate shown alongside them follows the same "Immediately" classification as rejection rate in the table above.

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

**Supervisor (web):** dashboard, task list and week view, create task, edit task, assign, review reports, technician roster, a technician's board, review a technician's skills, edit a technician's photo, tickets list, review a ticket.

**Manager (web):** roles & permissions — everything else a manager sees is whatever the matrix currently grants a manager, which starts out as everything on the supervisor list above, plus review reports.

**Technician (phone):** my week, task detail with photos, report form, my progress, my skills, my tickets, my profile (own photo, language, password).

**Customer (public, no login):** the feedback form — reached only through the emailed link, never linked from anywhere inside the app; and the ticket form — meant to be shared/discoverable, unlike the feedback link.

**The dashboard is a summary, not a new source of truth.** It shows who's available and every open task's lead and schedule at a glance — country-scoped, same as the roster and week view — but nothing lives only there; task list and week view remain the detailed screens for actually managing that work.

Nineteen screens plus two public pages. That is the whole application.
