# Walking through the system, role by role

Before go-live: sign in as each person and check they see what they should, can
do their work, and **can't** do what isn't theirs. The automated access matrix
(`tasks/test_access_matrix.py`, `tasks/access_matrix.json`) already checks every
page and app screen for every role on each run; this is the human check — does
each person's day make sense.

**Accounts:** `python manage.py walkthrough_accounts --country AE --customer "Demo Chain Fitness"`
(development only) makes one per role, `walk-<role>`, in the UAE, and a portal
login for that customer; the sign-ins are in `walkthrough_logins.txt` (git
ignores it). `--delete` switches them off afterwards.

**For each:** sign in on the website, then in the app (the customer: the portal).
Tick what works; note anything that's wrong or confusing, with the page.

---

## Technician (`walk-technician`)
Should:
- [ ] App opens on **Today**: the next job, its tap (Confirm → On the way → Arrived → Start), directions.
- [ ] Do a visit end to end: taps, photos (Serial, Before, After), the report, the customer's signature.
- [ ] An installation: each machine checked off, its serial, "tested — working", the handover.
- [ ] A loading: each serial or "not reachable (packed)", the dispatch photo.
- [ ] My schedule (week / month / year), My progress, My skills, My profile.
Must not:
- [ ] See other people's tasks, the dashboard, customers, requests, stock, the team, settings.

## Supervisor (`walk-supervisor`)
Should:
- [ ] Task list (their country), create a task, edit and assign the tasks they own.
- [ ] Approve a technician's report; request a time change on a manager-locked task.
- [ ] Their technicians: board, progress, skills, availability, hours; Team progress; the team map.
- [ ] Customers and machines: **view** only.
Must not:
- [ ] Edit a task someone else owns; see another country's tasks; see requests, stock, the dashboard.
- [ ] Edit a customer or a branch; reset anyone's password.

## Manager (`walk-manager`)
Should:
- [ ] Dashboard (their country): who's available, what needs someone first.
- [ ] Step in on any task in their country; approve and close; cancel; book a new visit after a stop.
- [ ] **Plan a loading or an installation's machines; send loaded machines on.**
- [ ] Parts to prepare, stock (and counts), parts catalogue; Staff accounts (send a setup link, unlock).
- [ ] Customers: view and edit.
Must not:
- [ ] See another country's tasks; see requests; set a password or change a login email; Roles, Notifications, Audit log.

## Country Support Manager (`walk-country-support`)
Should:
- [ ] Customer requests (their country): review, reply, "What happens next?", escalate, close.
- [ ] Internal tasks; customers (view and edit); machines.
- [ ] A new request reaches them: email, bell, phone.
Must not:
- [ ] Plan tasks or the team; another country's work; settings.

## Technical Support Manager (`walk-support-manager`)
Should:
- [ ] Every country's requests; escalations to them, decided.
- [ ] Final approval of reports; the company summary of alarms.
- [ ] Customers' portal logins (invite, links, unlock).
Must not:
- [ ] Plan a loading or installation's machines; set a password; Roles, Notifications, Audit log.

## Operations Manager (`walk-operations-manager`)
Should:
- [ ] Every country (the country switcher, the all-countries calendar); step in anywhere.
- [ ] Requests, customers and their logins; the monthly report; skills, brands, countries.
Must not:
- [ ] Plan a loading or installation's machines; set a password or change a login email; Roles, Notifications, Audit log.

## Project Manager (`walk-project-manager`)
Should:
- [ ] Every country's tasks and team, like a manager; **plan loadings and installations, send machines on.**
Must not:
- [ ] Customer requests (none); edit a task someone else owns.

## Warehouse Manager (`walk-warehouse-manager`)
Should:
- [ ] Stock: counts, the machines' opening stock upload, "Loaded, not installed yet".
- [ ] Parts to prepare; prepare and ship part shipments; receivings and loadings.
Must not:
- [ ] Ordinary visits, customers, requests, the team.

## Admin (`walk-admin`)
Should:
- [ ] Everything above; Roles & permissions; Notifications; the Audit log.
- [ ] Set a temporary password, change a login email (staff and customers) — the only one.
- [ ] Add staff; import customers.

## Customer (`walk-customer@example.invalid`, the portal)
Should:
- [ ] Home: the next visit, requests and their progress, in English (Arabic if their language).
- [ ] Send a request (a machine, photos), add a branch, add a machine; see visits, reports (PDF), rate a visit.
- [ ] Settings: contact details, language, password.
Must not:
- [ ] Anything of another customer (try changing a number in the address: it must say not found).
- [ ] Sign in to the staff app ("This app is for our staff…").

---

**Found something?** Note the role, the page, what you expected and what happened.
