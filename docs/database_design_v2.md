# Spots Solutions — Technician App
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
| iso_code | char(2) | |
| timezone | varchar | IANA name, e.g. `Asia/Riyadh` |
| currency_code | char(3) | |
| is_active | bool | |

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
**One skill per brand. Three rows, not twelve.**

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| brand_id | FK → brand | |
| name | varchar | |
| is_active | bool | |

A finer split — mechanical versus electronic, strength versus cardio — is tempting but wrong at the start. Supervisors would be judging categories they have never had to name, so they would guess. One skill per brand is something they can answer confidently today, from memory, in a single meeting.

**Splitting later is easy; merging later is not.** Add a skill when the data justifies it and set levels only for the technicians it affects. Start with twelve wrong skills and you have twelve columns of bad data with no way to tell which parts were right.

**Maintenance burden decides whether this survives.** Three rows per technician get updated. Twelve do not, and a stale matrix is worse than none because people trust it.

**The test for whether a new skill should exist:** can you name one technician who is good at it and another who is not? If not, it is a category of machine, not a skill.

**Let the data find the split.** After a year, look at callback rates per technician broken down by machine category. If someone's record on a brand is clean for strength equipment and poor for cardio, that is evidence for splitting — and it tells you exactly where the line goes, instead of guessing now.

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

**Freelancers see only their own tasks and the sites attached to them** — never the customer list or other technicians' records. A freelancer may work for a competitor next month.

### technician_skill
The capability matrix. Answers "can he do this job", separately from "will he do it well".

**There are no certificates.** The level is a supervisor's judgement, so the table records whose judgement it was.

| Column | Type | Notes |
|---|---|---|
| id | PK | |
| technician_id | FK → technician | |
| skill_id | FK → skill | |
| level | int | 1–4 |
| set_by_id | FK → technician | which supervisor decided |
| set_on | date | |
| note | varchar | nullable |

Unique on (technician_id, skill_id).

### How a supervisor sets a level

**The level is a count of evidence, not an opinion.** The supervisor answers one factual question per brand: *how many times has this man led this brand's work alone, and did it come back?*

**1 — Has never led this brand alone.** Helper only.
**2 — Has done it under supervision and it went fine.** Leads simple jobs, calls for help on faults.
**3 — Has led this work alone, repeatedly, with no callbacks.** Send him and do not worry.
**4 — Others call him when they are stuck.** Can train.

Each of these is checkable. "Has he led a Panatta job alone?" has a yes or no answer two supervisors would agree on. "Is he skilled?" does not.

Write this on one page in Arabic and English. Without certificates, these four sentences are the only thing holding the levels together across countries.

**Once there is history, show it before he decides.** The level screen should display the facts first — *led 11 Panatta tasks, 1 came back within 30 days* — so the supervisor judges against the record rather than from memory.

**Level 4 needs two supervisors to agree.** It is the level that gets inflated, because it usually carries pay or status. Requiring a second name costs nothing and keeps the top of the scale meaningful.

**Recording `set_by_id` is what keeps it honest.** If a level is questioned later you know whose judgement it was, and supervisors are measurably more careful when their name is attached.

**Set the initial levels in one room, together.** Get every supervisor around a table for two hours and go through the technician list brand by brand, agreeing out loud. The arguments in that room are the point — that is where the definitions get calibrated. Setting levels individually guarantees drift from the first day.

**Let the data correct the judgement over time.** After a year, compare first-time fix rates by level. If level 3 technicians on a brand are no better than level 2, either the definitions are wrong or someone is inflating. The event log becomes the check on the opinion.

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
| source | varchar | phone, whatsapp, email, internal |
| is_warranty | bool | nullable until known |
| billing_type | varchar | warranty, contract, chargeable, goodwill |
| reported_at | timestamptz | |
| promised_at | timestamptz | what the customer is owed |
| scheduled_for | timestamptz | nullable — the day the supervisor planned |
| status | varchar | see below |
| created_by_id | FK → user | |

**Status flow:** `new` → `assigned` → `accepted` → `in_progress` → `completed` → `closed`.
Plus `blocked` and `cancelled` as endings.

`promised_at` and `scheduled_for` are different. The first is the customer's deadline, the second is the slot you planned. A task due Tuesday and a task planned for Tuesday are not the same thing.

**`blocked` is a legitimate outcome** — gym closed, no key, customer absent. It must not count against the technician.

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

**Event types:** created, assigned, reassigned, rescheduled, accepted, en_route, arrived, blocked, started, completed, report_submitted, report_rejected, report_approved, closed, reopened, cancelled.

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

---

## 6. Measuring reliability

Capability is the skill matrix — a supervisor's judgement about what a technician can do. Reliability is different: it is computed from `task_event`, and nobody types it.

### What you can measure

| Metric | How | Available |
|---|---|---|
| On-time arrival | `arrived` before `promised_at`, excluding `blocked` | Immediately |
| Acceptance latency | Median minutes from `assigned` to `accepted` | Immediately |
| Report rejection rate | rejected ÷ submitted | Immediately |
| Tasks led vs helped | Count by `role` | Immediately |
| First-time fix rate | Completed tasks with no new task on the same asset within 30 days | Around month nine |

**First-time fix rate is the best of these and the last to arrive.** It needs the asset register, and assets only come into existence as machines get serviced. Do not promise this number to anyone in year one.

### Roll it out in three stages

**Months 1–3: measure nothing, show nothing.** Collect events only. Any figure computed on a few weeks of data is noise, and showing noise once destroys trust in the system permanently.

**Months 4–8: show facts, not scores.** Tasks completed, on-time percentage, rejection rate. Plain numbers a technician can check and argue with. No ranking, no single combined score.

**Month 9 onward: add first-time fix, broken down by brand.** This is when the original question — who can I depend on — becomes genuinely answerable.

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
- Customer portal — `task.source` and the `new` status are already in place for it
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

**Supervisor (web):** task list and week view, create task, assign, review reports.

**Technician (phone):** my week, task detail with photos, report form.

Six screens. That is the whole application.
