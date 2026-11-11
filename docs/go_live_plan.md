# Go-live plan

What has to happen before the website, the customer portal and the phone app
are used for real. Items are added here as they're agreed; each is ticked off
(and moved into `database_design_v2.md` / `access_rules.md` once built).

## 1. Before go-live: to build

### ✅ Personal portal logins (several people per customer) — built 2026-11-11

Now described in `database_design_v2.md` (`portal_user`). Agreed 2026-11-11. Today a customer has one portal login (`customer.user`),
shared by everyone at the company. That hides who sent what, and an employee
who leaves keeps access until the shared password changes for everyone. Fine
while few customers use the portal; it must be fixed before it's rolled out
widely.

- **One customer account, several people.** The customer stays one account —
  all its branches, requests, visits, reports and machines together, as
  today. Each person at the customer has their own email and password.
- **Everyone sees the whole customer**, every branch, exactly as today's
  single login does — so head office sees every branch's problems in one
  place. (Each branch's contact already gets the emails about its own
  visits and shipments, without a login.)
- **Each request shows who sent it** ("Sent by Omar"), and its confirmation
  goes to whoever sent it.
- **Our staff add and remove people**, on the customer's Portal login section
  (the same invitation email as today). Removing one person leaves the others
  untouched. No customer-side admin role: the customer asks us.
- **Underneath:** a small table of a customer's people (their login and name)
  replaces the single `customer.user`. The existing login moves over as the
  first person, so nothing changes for current customers. The portal's pages
  stay as they are — they already show the whole customer.

**Decided against, for now (2026-11-11):** limiting a person to chosen
branches. It would need a branch filter on every portal page (requests,
visits, reports, machines, the bell, the home counts, the new-request form),
where one forgotten filter shows a branch manager another branch's data — for
a need nobody has raised. Add it only if a chain asks; nothing above has to be
undone for it. Likewise a customer admin role who manages their own people.

## 2. Hosting and running

- **Hosting:** `render.yaml` is prepared for Render (Frankfurt; deploys only
  after the GitHub tests pass). Confirm the choice, then the real domain, the
  portal's own address (`PORTAL_HOST`) and the staff one (`STAFF_HOST`).
- **Live settings:** secret key, debug off, real mail credentials, the app
  sign-in token's expiry.
- **Already built in (2026-11-11), nothing to do but keep:**
  - a Content-Security-Policy on every page (`spots.middleware`): scripts
    only from the site, unpkg and the page's own (with a per-request nonce);
    a new outside source (a font, a map, a widget) has to be added there;
  - "Forgot your password?" (portal and staff) sends at most one link per
    address every 15 minutes (`spots.throttle`), so it can't flood an inbox
    or use up the mail account's daily allowance;
  - database connections are reused for a minute (`DB_CONN_MAX_AGE`); the
    cache lives in the database, its table made at start-up
    (`createcachetable` in `docker-entrypoint.sh`).
- **Backups** of the database, and a check that one can be restored.
- **A daily scheduled job**, so warnings that today only show in the bell
  (a report waiting over 2 working days) also reach people's phones.
- **The phone app:** real builds with EAS (`eas build`, then `eas submit`),
  pointing at the live server.

## 3. Accounts to create on the live server

Real names and emails are still needed from the owner; accounts get a setup
link, never a password sent in chat.

- **Operations Manager** (Mohamed) and a **Country Support Manager** per
  country — UAE, KSA, Qatar, Oman, Egypt (Ahmed for Egypt).
- **Warehouse Managers:** Ali (UAE), Shady (KSA), Hany (Qatar). In Egypt the
  supervisor runs the warehouse. Each country's warehouses are added as sites
  under its internal "Profit Sports Solutions" account ("Our warehouses").
