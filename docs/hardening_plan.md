# Pre-launch hardening plan

One plan, built once, so nothing has to be reopened after go-live. It merges:
the portal and role walkthroughs (browser, 13 Nov 2026), and three full
reviews — every business process, every action by every role, performance and
data integrity. Each item: what's wrong → the fix (website, portal, app,
emails together) → the test that locks it. Severity: **H** high, **M** medium,
**L** low.

Decisions already made by the owner are marked ✔; open ones are in §8.

---

## 1. Permissions — every action, not more, not less

| # | Sev | What's wrong today | The fix |
|---|---|---|---|
| 1.1 | H | **Staff accounts ignore rank and country**: a country Manager can disable the Operations Manager's login, sign out his phones, act on staff in other countries. **"Enable" brings back someone who left the company.** | One rule (`people.permissions.can_manage_account`), web and app: Admin over everyone; Operations Manager over everyone but the Admin; Manager / Project Manager / Tech Support Manager over Supervisors, Technicians, Warehouse Managers and Country Support only — in their own country unless they reach all countries. "Enable" refused for anyone marked as left. |
| 1.2 | H | A **Supervisor can reactivate** a deactivated technician (deactivating needs a manager). | Reactivating needs the same as deactivating, through 1.1's rule. |
| 1.3 | M | **One person can give both report approvals**; any manager approves the supervisor step on any task, unrecorded. | Supervisor approval: the task's owner, or someone stepping in (recorded). Final approval refused to whoever gave the supervisor approval. |
| 1.4 | M | A task's **owner can be blanked**, after which anyone in the country can change it; a task can be created with no owner. | The owner is required (changed only by handing over); every new task gets one. |
| 1.5 | M | Actions that **skip the owner check**: add a document, edit a part needed, create a follow-up, request feedback, **correct a customer-signed report** (any task, any country, unrecorded). | All go through the task-owner check (owner, or a recorded step-in). |
| 1.6 | M | **Internal tasks**: anyone at manager level, the Warehouse Manager and Country Support can note, reassign or close anyone's. | See ≠ act: act = the person it's for and the opener (managers may step in, recorded). |
| 1.7 | M | Website availability: a Supervisor can mark the Manager or the Admin unavailable (the app is stricter). | The website uses the app's rule (people you oversee). |
| 1.8 | L | Customer records: adding a branch, creating one from a request and editing a branch each use a different switch; branch editing is limited to the viewer's selected country. | One rule for customers and branches (§3 decision ✔), all-country roles included. |
| 1.9 | L | App lets staff reply on a request whose visit is closed; the website doesn't. | The app follows the website. |
| 1.10 | M | Custom roles inherit their base role's fixed powers (staff accounts, step-in, loading planning) — the access rules contradict themselves. | Per decision D1. |
| 1.11 | L | `docs/access_rules.md` points to a file that doesn't exist. | Corrected; the rules document names where each rule lives. |

**Locked by:** `tasks/test_action_matrix.py` + `tasks/action_matrix.json` — every
state-changing action on the website, the portal and the app (every task-page
and request-page action, staff accounts, people, roles, customers, stock,
portal, every non-GET API endpoint), by every role, a custom role, a
deactivated user, two customers and someone signed out, against own / another
owner's / another country's / unowned objects. The outcome is read from the
database (done, done-stepped-in, no change, refused, not found), not the
status code. Two guards: an action added later without a matrix row fails the
test; a website action and its app twin must give the same answer.

## 2. Data integrity — nothing duplicated, nothing lost

| # | Sev | What's wrong | The fix |
|---|---|---|---|
| 2.1 | H | **An audit failure can silently undo the save it records** (inside a transaction), and the IP is taken from a header the client controls — `X-Forwarded-For: unknown` triggers it. | The audit entry is written in its own savepoint; the IP is validated and taken from the proxy's entry, else the connection. |
| 2.2 | H | **Double click / two people = duplicate tasks** (converting a request, New task, the portal's New request with slow photo uploads), two crews, two emails. No row locking anywhere. | The request (and the source task for next steps / follow-ups) is locked while it's converted; a second attempt is told it's already done. Every form's button disables itself after the first press. |
| 2.3 | M | App retries duplicate **"On the way"** (two emails); stock counts by two people double the correction. | Taps and stock counts lock their row; a repeated tap does nothing. |
| 2.4 | M | "One serial per machine at a branch" is only checked by forms. | A database rule (after checking existing data has no duplicates). |
| 2.5 | M | **Converting another country's request stores the visit time 1–2 hours off**, and the customer is emailed the wrong time. | The form reads times in the request's country. |
| 2.6 | H | **Phone notifications are sent inside the request**, one by one (5 s timeout each): a slow push service stalls the whole site. | Sent in the background, one call per language. Also: the report "Review" step sends real pushes for a task it then throws away — pushes wait for the save to be kept. |

**Locked by:** tests for each (double conversion → one task; bad header →
saved, IP empty; repeated tap → one event; other-country time).

## 3. Processes — new customer, new branch, and every flow end to end

**3A. New customer / new branch / its machines** (the gap the owner found) — decisions ✔:
creating a customer or a branch: **Manager, Project Manager, Technical Support
Manager, Operations Manager, the COO, Admin**; machines appear on the portal **only after
handover**; a branch created by staff → **the customer gets a short email** (EN + AR).

| # | Sev | The fix |
|---|---|---|
| 3.1 | H | **"+ New branch" (and "+ New customer") in the installation step** of a loading, for the roles above — the planner is never stuck. Duplicate check ("Fitness First already exists — use it?"). |
| 3.2 | H | **Phone / email / WhatsApp**: staff "New request" gets "A new branch", and a request for an unknown caller (company and branch typed in, like the portal) — Country Support can log it; the roles above create the customer/branch from it in one click. |
| 3.3 | M | **One installation per new branch**: sending loaded machines on to a branch that already has an open installation adds them to it (no second, empty task); the customer's request follows the real one. |
| 3.4 | H | **Installation report corrections update the customer's machines** (a corrected serial replaces the wrong one; a machine marked done later is added). |
| 3.5 | H | **Cancelling an installation releases its loaded machines** (they can be sent on again; stock counts them back). |

**3B. Other flows**

| # | Sev | What's wrong | The fix |
|---|---|---|---|
| 3.6 | H | **A cancelled visit leaves its request stuck**: the customer reads "we're preparing your visit" forever; nobody is told. | Cancelling a visit from a request puts the request back on the desk (bell), and tells the customer (EN + AR). |
| 3.7 | M | Site consultation and online meeting are treated as **installations** (site-not-ready warnings, warehouse sees them, only planners can plan). | Per decision D8. |
| 3.8 | M | Deactivating a supervisor moves their assignments but **not the tasks and requests they own**. | Ownership moves to the successor too; a bell warning for any open task whose owner is inactive. |
| 3.9 | M | A request split over two visits shows only the first one's progress ("Done" while the other is pending). | Progress from the least advanced visit. |
| 3.10 | L | Parts to prepare keeps finished jobs; nobody is told when a customer adds a machine on the portal; follow-ups don't email staff; a second follow-up is impossible. | Finished jobs drop off; a bell item for machines to confirm; the follow-up email; follow-up for anything still missing. |
| 3.11 | — | Portal walkthrough (done, in test): on-site visit dates, "Your visit now", aligned progress, tick boxes, the one-branch case, "Choose the branch/brand", "Branch" wording, machine cards checked before moving on (incl. the two repair photos), "Request … sent", "Not ranked yet", no sideways shift. Home: "Repairs with parts ready to prepare" for whoever prepares parts. |

**Locked by:** `tasks/test_processes.py` — each flow start to finish (portal
request → close; phone request; new customer + branch → loading → installation →
handover → machines on the portal; cancel and re-plan; parts; part shipment;
internal task; staff leaving), asserting at each step who sees it and who is
told (bell, email, push, portal).

## 4. Performance — fast at full scale

| # | Sev | What's wrong | The fix |
|---|---|---|---|
| 4.1 | H | **Portal home / visits / reports / requests: queries per visit** (≈500 for a chain with 200 signed visits). | Visits and requests read in a fixed number of queries; the "changed after signing" check only on the visit page. |
| 4.2 | H | **The bell loads every unseen row on every page** (the app asks every 30 s) and one query per "parts arrived". | The newest 10 per kind plus a count; related rows read with them. |
| 4.3 | M | Parts to prepare, stock, "not installed yet", the stock import: per-row queries or whole-history scans. | Read once, filtered in the database. |
| 4.4 | M | The audit trail adds ~3 queries per save; the Audit log counts and scans the whole table per page. | The actor read once per request; the page uses the known record kinds and indexed date bounds. |
| 4.5 | L | Indexes: task list order, date filters that can't use the index, unseen notifications, overdue-unscheduled walk. | Indexes and range bounds. |
| 4.6 | H | **Server sizing**: 3 single-thread workers, 30 s timeout, 512 MB, PDFs in unbounded threads, media served by the app, scheduled jobs not scheduled anywhere. | Threaded workers (2 × 4), 60 s, recycled; PDFs one at a time; the scheduled job on Render (reminders, alarms, audit retention, sessions); Sentry tracing at 10 %; media to object storage before a second server (D9). |

**Locked by:** `tasks/test_performance.py` grows data where the problems were
(signed reports, undated visits, parts needed, bell rows, audit rows) and adds
the portal, the bell, parts to prepare, stock and their app twins.

## 5. One consistent look

Section actions one style; submit buttons placed the same way everywhere;
headings in order; the site's own translated file picker everywhere; no
"← Back" on top-level pages; Panatta PAK only on Panatta tasks; names isolated
from dates in Arabic on every staff page. **Locked by** `tasks/test_ui_standard.py`
(the scan: Arabic-safe CSS, every button styled, every page titled, file
pickers, isolated names) and `docs/ui_standard.md`.

## 6. Finally — as each person, in the browser

Every role on the website and the app, English and Arabic, phone width: the
walkthrough in `docs/role_walkthrough.md`, with the action matrix as the
checklist. Then the go-live items in `docs/go_live_plan.md`.

## 7. Order of work

1 Permissions → 2 Data integrity → 3 Processes → 4 Performance → 5 Look → 6
Walkthrough. Each phase: full test suite, browser check as the affected roles,
one commit.

## 8. Decisions (owner, 13 Nov 2026)

- ✔ **D1 Custom roles** get only the switches ticked for them on the Roles
  screen; fixed powers (staff accounts, stepping in, planning loadings) stay
  with the built-in roles.
- ✔ **D2 Staff accounts** follow rank and country as in 1.1 (the COO and the
  Operations Manager over everyone but the Admin).
- ✔ **D3** The supervisor's approval and the final approval are given by two
  different people.
- ✔ **D6** Supervisors may edit technicians' name, phone and photo; Project
  Manager and Tech Support Manager may add countries, brands and skills; the
  manager tier may edit commercial terms.
- ✔ **D8** Site consultation and online meeting become a "consultation" kind
  of visit; relocation stays an installation.
- ✔ **D10 A new built-in COO role**: everything in every country like the
  Operations Manager, ranked above him in staff accounts, below the Admin;
  creates customers and branches (3A).
- **D9 Hosting** — researched 13 Nov 2026 (Render, Cloudflare, AWS UAE,
  DigitalOcean; prices from their pages that day). Recommendation, for the
  owner's approval:
  - **Start (go-live), ≈ $85–90/month:** Render Pro workspace ($25), one
    Standard web server 1 CPU / 2 GB ($25), Render cron jobs (~$4), Postgres
    1 GB ($19) + ~20 GB storage (~$6), photos and reports on **Cloudflare R2**
    (EU jurisdiction; first 10 GB free, no download fees), a nightly off-site
    copy (~$2). 7-day point-in-time recovery; deploys without downtime once
    the disk is gone.
  - **Grow, ≈ $185–200/month**, when monitoring says so (pages p95 > 0.8 s for
    a week, CPU > 70 % / memory > 80 %, any out-of-memory restart, ~100 phones
    signed in at once, DB cache hit < 99 %): a second web server, a larger
    database with a standby (automatic failover in ~30 s).
  - **Data location (for legal):** Render Frankfurt + R2 EU keeps everything in
    the EU. Saudi PDPL allows transfers abroad with safeguards (SDAIA standard
    clauses, a risk assessment); UAE PDPL's own conditions to check. If legal
    or a client contract requires hosting in the UAE/KSA, the alternative is
    AWS UAE (≈ $120–250/month, more upkeep).
  - **The owner creates** (company email and card, two-step sign-in, invite
    the developer): Render Pro, Cloudflare (R2), the off-site backup
    location, Sentry, DNS, the email provider. **The developer configures**
    render.yaml, R2 in Django (permission check, then a short-lived signed
    link), moving existing files, backups and one test restore, alerts.
