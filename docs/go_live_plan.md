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
  - every upload, customers' and staff's, must really be what its name says
    (`spots.uploads.SafeFileValidator`: a photo opens as that image, a PDF
    starts with `%PDF-`, a video carries its marker) — a renamed page or
    program is refused; uploaded files are served so nothing in them can run
    (`spots.media`: a "sandbox" policy; anything not a photo, video or PDF
    downloads instead of opening).
- **Backups** of the database (daily, kept at least 30 days, a copy outside
  the hosting company), **and a restore actually tried once** — a backup
  never restored is only a hope. Uploaded files (photos, reports) backed up
  too, not just the database.
- **Monitoring:** switch on error monitoring (Sentry — `SENTRY_DSN`, already
  wired in `spots/settings.py`), and an uptime check that alerts someone when
  the site or the portal stops answering.
- **A scheduled job** (every 15 minutes, and once a day), so the warnings that
  today only show in the bell reach people's phones: a task still blocked, one
  with no day, a report waiting — and later the daily summary by email.
- **The phone app:** real builds with EAS (`eas build`, then `eas submit`),
  pointing at the live server.
- **Customer emails from your own domain** (added 2026-11-12). Every email to a
  customer is signed by Customer Service and its replies go to
  `service@profitsports.solutions` (`CUSTOMER_SERVICE_EMAIL`). On the live server,
  send through a proper email provider for `profitsports.solutions` (Zoho Mail's
  SMTP, or Postmark / Amazon SES), set `CUSTOMER_FROM_EMAIL` to
  `Profit Sports Solutions <service@profitsports.solutions>`, and add the domain's
  SPF, DKIM and DMARC records — otherwise customer emails land in spam. (In
  development they go through a Gmail account, which sends as itself.)
- **Customer replies by email land in their request** (later, once the provider
  above is in place): an inbound address per request, so a customer who presses
  "Reply" adds to the request's conversation instead of the team copying it
  from the mailbox. Until then the team's mailbox is the safety net.
- **Google Maps key for Android** (the app's Team map, `react-native-maps`,
  added 2026-11-12). Expo Go needs none, and iPhones use Apple Maps with no
  key; the Android store build shows a blank map without it. In Google
  Cloud (an account with a card on file; this team stays inside the free
  allowance): enable "Maps SDK for Android", create a key restricted to the
  app's package and its Play signing SHA-1 (from Play Console, after the
  first upload), then add it to the app's config as the `react-native-maps`
  plugin's `androidGoogleMapsApiKey` — from an environment variable through
  `app.config.js`, never written into `app.json` — and build again.
- **A staging copy** of the site (its own address, its own database with test
  data): every release goes there first and is tried before the live site.
- **Smaller releases:** `app-today` holds 135 commits not yet in `master`.
  Merge it once, then release in small steps (a branch per piece of work,
  through staging), so each release is easy to check and to roll back.
- **Dependabot** (`.github/dependabot.yml`, added 2026-11-11) opens a pull
  request when a library has an update or a security fix; switch on
  "Dependabot alerts" and "security updates" in the GitHub repository's
  Settings → Code security, and look at its pull requests weekly.

## 2b. Personal data (UAE and Saudi data-protection law)

- **A privacy notice** on the portal (and linked from the app's sign-in):
  what we keep about customers and staff, why, who sees it, how long.
- **How long data is kept:** a written rule for requests, photos, reports,
  logins and the Audit log (for example: reports as long as the machine is
  under service; logins of people who left removed after N months).
- **Export and deletion:** how a customer asks for their data, or for it to
  be deleted, and who at the company handles it — and where photos and
  backups are kept (which country).

## 3. Accounts to create on the live server

Real names and emails are still needed from the owner; accounts get a setup
link, never a password sent in chat.

- **Operations Manager** (Mohamed) and a **Country Support Manager** per
  country — UAE, KSA, Qatar, Oman, Egypt (Ahmed for Egypt).
- **Warehouse Managers:** Ali (UAE), Shady (KSA), Hany (Qatar). In Egypt the
  supervisor runs the warehouse. Each country's warehouses are added as sites
  under its internal "Profit Sports Solutions" account ("Our warehouses").
