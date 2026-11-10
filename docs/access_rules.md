# Who sees, who changes — the rule book

What each person can see and change, for tasks and internal tasks. The code
follows this one place (people/access.py); keep the two in step. Agreed with
the owner, November 2026.

## Roles

| Role | Area |
|---|---|
| Technician | their own tasks |
| Supervisor | their country |
| Warehouse Manager | their country, stock-related work only |
| Country Support Manager | their country |
| Manager | their country only (one Manager per country) |
| Project Manager | every country — like a Manager, no customer requests (no Tickets) |
| Technical Support Manager | every country |
| Operations Manager | every country |
| Admin | every country |

## Tasks (field work)

**Seeing.** Everyone who coordinates work sees their whole area:

| Role | Sees |
|---|---|
| Technician | tasks they're on (lead or helper) |
| Supervisor | every task in their country |
| Warehouse Manager | stock-related tasks in their country: part shipments, receiving, loading, installations |
| Country Support Manager | every task in their country (to answer customers); changes none |
| Manager | every task in their country |
| Project Manager, Technical Support Manager, Operations Manager, Admin | every task, every country |

**Changing — one owner per task.**
- Every task has **one owner**: the person who created it, or the supervisor it
  was given to ("Supervisor" when it's created). Stored as the task's
  responsible supervisor.
- **Only the owner changes it**: its details, the visit time, the lead and
  helpers, the actions on it.
- The owner can **hand it over** to someone else (Edit task → Supervisor), for
  travel or leave. The new owner is told.
- **Stepping in.** When the owner is away, these can change it anyway; each
  change is recorded in the history and the owner is told:
  - the **Operations Manager** and **Admin** — any task;
  - the country **Manager** — tasks in their own country.
  The Technical Support Manager and the Project Manager change only the tasks
  they own.
- **Everyone else** who can see it **asks** the owner (a team message to them),
  who decides.
- A time a manager-level owner set is **locked** for a supervisor: he asks to
  move it, and the owner approves or refuses (as before).
- The **technician** always does their own steps and report.
- Tasks with no owner yet (from before this rule) stay open to whoever the
  role allows, until one is set.

### The work report: review, then approval

1. The technician submits it → **the task's supervisor (owner)** reviews it;
   he is told on his phone and it's in his *Waiting for me*. A supervisor's own
   report skips this step.
2. Reviewed → **the Technical Support Manager** gives the final approval,
   which closes the task. Every Technical Support Manager is told on the phone
   and it's in their *Waiting for me* (and the Dashboard's top).
3. The **Operations Manager** and **Admin** may approve instead — recorded in
   the history ("Instead of the Technical Support Manager"). They aren't told
   of each report — but a report **waiting more than 2 working days** shows in
   the Operations Manager's bell and Waiting for me until it's approved. A Manager, the Project Manager and a supervisor see the
   report but don't approve it.

## People (their pages, schedules, skills, progress)

- **Admin:** everyone.
- **Manager, Project Manager, Technical Support Manager, Operations Manager:**
  supervisors and technicians.
- **Supervisor:** technicians.
- **Warehouse Manager and Country Support:** nobody.

## Internal tasks (office work)

Kinds: **Part request** (anyone, technicians included — part codes and
quantities from the parts list, or words; "for task UAE-0015"), **Stock
request**, **Vendor follow-up**, **Other** (office roles).

- Status: **Open → Waiting** (vendor, delivery, answer) **→ Closed** with a
  result; or turned into a field task. An optional **due date**: overdue shows
  red.
- **Owner:** the person it's for. They and whoever opened it act on it.
- **Seeing:** the person it's for and the opener; the country Manager and
  Warehouse Manager (part and stock requests) in their country; Project,
  Operations, Technical Support Manager and Admin everywhere.
- **Every action tells the people involved** (phone + bell until opened):
  created or reassigned → the person it's for; new note → the person it's
  for, the opener and anyone who wrote a note; waiting, overdue, closed → the
  person it's for and the opener (and the field task's lead when it's for one).
- Can be linked to a field task; that task lists its internal tasks.

## Customer requests

Unchanged: Technical Support Manager, Country Support Manager, Operations
Manager and Admin (the Project Manager has no Tickets).

A **new request** is told to the Technical Support Manager and that country's
Country Support Manager — the roles chosen on the admin's Notifications page —
by email, in the bell and on their phones (opening the request in the app).
