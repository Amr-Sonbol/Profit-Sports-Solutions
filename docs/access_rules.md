# Who sees, who changes — the rule book

What each person can see and change, for tasks and internal tasks. The code
keeps these rules in `people/permissions.py` (accounts, customers, loading),
`tasks/views.py` (`can_step_in`, `_require_task_owner`, approvals, internal
tasks) and the Roles & permissions switches; keep them in step with this page.
Locked by tests: `tasks/test_access_matrix.py` (who opens each page and app
screen) and `tasks/test_action_matrix.py` (who can do each sensitive action). Agreed with
the owner, November 2026.

## Roles

A role is what someone **can do**, not what they're **called**: each person
can have a job title (a General Manager with the Operations Manager's role, a
"Customer Service Coordinator" with Country Support's), shown wherever they're
listed. Give someone the role whose powers fit, and the title they're known
by. For a position no role fits, the Admin makes a **role of their own** on
Roles & permissions (a "Coordinator"), based on a built-in role. **It has only
the switches ticked for it** (owner, 13 Nov 2026): none of the built-in roles'
fixed powers — managing staff logins, stepping in on others' tasks, approving
reports, planning loadings, creating customers, watching the whole company —
and it stays in its own country.

| Role | Area |
|---|---|
| Technician | their own tasks |
| Supervisor | their country |
| Warehouse Manager | their country, stock-related work only — and repairs that need parts, to prepare them (*Parts to prepare*) |
| Country Support Manager | their country |
| Manager | their country only (one Manager per country) |
| Project Manager | every country — like a Manager, no customer requests (no Tickets) |
| Technical Support Manager | every country |
| Operations Manager | every country |
| COO | every country — as the Operations Manager; above him on Staff accounts |
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
| Project Manager, Technical Support Manager, Operations Manager, COO, Admin | every task, every country |

**Changing — one owner per task.**
- Every task has **one owner**: the person who created it, or the supervisor it
  was given to ("Supervisor" when it's created). Stored as the task's
  responsible supervisor.
- A machine **sent on to the next step** (loading → installation) keeps its
  supervisor within the same country. Going to **another country**, the manager
  must choose **"Supervisor there"**, someone in that country, so the new task
  is never left without an owner.
- **Only the owner changes it**: its details, the visit time, the lead and
  helpers, the actions on it.
- The owner can **hand it over** to someone else (Edit task → Supervisor), for
  travel or leave. The new owner is told.
- **Stepping in.** When the owner is away, these can change it anyway; each
  change is recorded in the history and the owner is told:
  - the **Operations Manager**, the **COO** and the **Admin** — any task;
  - the **Manager** and the **Project Manager** — a **loading or an installation**
    (they plan its machines), whoever created it;
  - the country **Manager** — tasks in their own country.
  The Technical Support Manager and the Project Manager change only the tasks
  they own.
- **Everyone else** who can see it **asks** the owner (a team message to them),
  who decides.
- A time a manager-level owner set is **locked** for a supervisor: he asks to
  move it, and the owner approves or refuses (as before).
- The **technician** always does their own steps and report.
- **A task always has an owner**: it can be handed over, never left blank;
  someone who can't own tasks (a field role) chooses one when creating it. A
  task with no owner (from before this rule) is changed only by those who
  step in, who give it one.

### The work report: review, then approval

1. The technician submits it → **the task's supervisor (owner)** reviews it
   (or someone stepping in — recorded);
   he is told on his phone and it's in his *Waiting for me*. A supervisor's own
   report skips this step.
2. Reviewed → **the Technical Support Manager** gives the final approval,
   which closes the task. Every Technical Support Manager is told on the phone
   and it's in their *Waiting for me* (and the Dashboard's top).
3. The **Operations Manager** and **Admin** may approve instead — recorded in
   the history ("Instead of the Technical Support Manager"). They aren't told
   of each report — but a report **waiting more than 2 working days** shows in
   the Operations Manager's and the COO's bell and Waiting for me until it's
   approved. A Manager, the Project Manager and a supervisor see the report but
   don't give the final approval.
4. **Two different people**: whoever gave the supervisor's approval can't give
   the final one too.
5. Correcting a report already filed (signed by the customer): its owner, or
   someone stepping in (recorded).

## Watching the whole company

The **Technical Support Manager** keeps an eye on every country (`tasks.alarms`)
— act, escalate, summarise, rather than every alert on their phone. The
**Operations Manager** gets only the urgent ones: the escalations below, and
its own reports waiting over 2 working days — not the summary.

- **Act:** whoever must do something is told at once, as above (the task's
  supervisor, the approver). Unchanged.
- **Escalate, in tiers:** in their bell, from **every country** whichever one
  they're looking at — each level up hears later, of what the level below
  didn't solve (`tasks.alarms.ALARM_HOURS`), so the Operations Manager's list
  stays short:

  | Problem | Technical Support Manager | Operations Manager |
  |---|---|---|
  | A task still blocked on site | after 2 working hours | after 2 working days |
  | A task with no day | after 24 working hours | after 3 working days |
  | A report waiting for final approval | (their own job) | after 2 working days |

  Live: each clears once it's handled. (On the phone once the scheduled job
  of the go-live plan runs.)
- **Summarise (the Technical Support Manager):** the top of their **Dashboard**, "Across the company" —
  blocked tasks (and those blocked over 2 working hours), tasks with no day
  after 24 working hours, new tasks in the last 24 hours, reports to review,
  to approve and waiting over 2 working days, new and escalated customer
  requests, locked staff and customer logins. Live counts, each opening its
  list. The same in the app.

## People (their pages, schedules, skills, progress)

- **Admin:** everyone.
- **Manager, Project Manager, Technical Support Manager, Operations Manager,
  COO:** supervisors and technicians. Their record (name, phone, photo…) is
  edited by whoever oversees them; a Supervisor edits technicians'.
- **Availability** (on the website and in the app): of the people one oversees.
- **Supervisor:** technicians.
- **Warehouse Manager and Country Support:** nobody.

## Internal tasks (office work)

One kind, the same name on the website and in the app: **Internal task** —
work one staff member hands another (office roles, not technicians). Buying
stock and chasing vendors is purchasing's work, outside the app; parts a field
task needs go through that task (a part shipment).

- Status: **Open → Waiting** (vendor, delivery, answer) **→ Closed** with a
  result; or turned into a field task. An optional **due date**: overdue shows
  red.
- **Owner:** the person it's for. They and whoever opened it act on it.
- **Seeing:** the person it's for and the opener; the country Manager and
  Warehouse Manager in their country; Project, Operations, Technical Support
  Manager and Admin everywhere.
- **Every action tells the people involved** (phone + bell until opened):
  created or reassigned → the person it's for; new note → the person it's
  for, the opener and anyone who wrote a note; waiting, overdue, closed → the
  person it's for and the opener (and the field task's lead when it's for one).
- Can be linked to a field task; that task lists its internal tasks.

## Customers

- **Creating a customer or a branch** (owner, 13 Nov 2026) — from a request,
  the Customers page, or while planning an installation: the **Manager, Project
  Manager, Technical Support Manager, Operations Manager, COO and Admin** (a
  Manager in their own country). Inviting its people to the portal: whoever
  manages portal logins.
- **Seeing and editing are two switches.** "View customers and sites" opens
  the customer list, a customer's page, its branches, contacts and machines.
  "Edit customers, sites and machines" changes them: the customer's details,
  its branches, confirming a machine the customer added.
- **A supervisor sees customers (to plan the work) but never edits them**; a
  technician sees neither. Everyone else who could before does both.
- Not editing: a machine the technician finds during a visit is saved with
  the visit's work ("Save this machine").

## Customer requests

Unchanged: Technical Support Manager, Country Support Manager, Operations
Manager and Admin (the Project Manager has no Tickets).

A **new request** is told to the Technical Support Manager and that country's
Country Support Manager — the roles chosen on the admin's Notifications page —
by email, in the bell and on their phones (opening the request in the app).

## Passwords and login emails

Only the **Admin** sets someone else's password or changes their login email —
staff and customers' portal logins alike (`people.permissions.can_set_password`):
whoever did either could sign in as them. And then only a **generated temporary
password**, read out by phone when the person has no email; they choose their own
at their next sign-in, and they're emailed about it. Nobody types in someone
else's password.

Everyone else who manages accounts sends the person a **link to their own email**
(a setup or password link) — the person chooses their own password, so nobody
else ever knows it.

**Who manages whose staff login** (unlocking, signing out of every phone, turning
it off or on, the setup link) — `people.permissions.can_manage_account`, the same
on the website and in the app:

| Who | Whose logins |
|---|---|
| Admin | everyone's |
| COO | everyone's but the Admin's |
| Operations Manager | everyone's but the Admin's and the COO's |
| Manager, Project Manager, Technical Support Manager | Supervisors', Technicians', Warehouse Managers' and Country Support's — in their own country (the Manager), or everywhere (who reaches every country) |
| everyone else, and any role made on Roles & permissions | nobody's |

Someone who **left the company** isn't switched back on from Staff accounts:
they're **reactivated** first, by whoever manages their login (never by a
Supervisor).

A changed login email is always told to the **old** address too.

## Loading and installation

Planning the machines on a **loading** or an **installation**, and **sending
loaded machines on** to an installation, are the **Manager's, the Project
Manager's and the Admin's** only (`people.permissions.can_plan_equipment`).
Everyone else — the Operations, Technical Support and Country Support
Managers, supervisors, technicians, and any role made later (a Coordinator,
say) — views them. The installer still checks each machine off on site
(what happened, its serial and photo) and records one that turned up but
wasn't on the paperwork: that's recording what happened, not planning.

