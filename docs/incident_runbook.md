# When something goes wrong: finding out, putting it right

For the Admin (and whoever runs the live server). Keep it short under pressure:
**find out → contain → put right → tell → learn**.

---

## 1. Find out what happened

| Question | Where to look |
|---|---|
| **Who changed this, when, from where, and what was it before?** | **Audit log** (website, Admin): filter by the record (customer, branch, task…), the person, the dates. Each change shows every field *before → after*, the person and their IP address. **Export (CSV)** to keep or send. |
| **What happened on a task, step by step?** | The task's page: its history (every tap with time, person and where it was made; reschedules; approvals; who stepped in). |
| **Who signed in, and was anyone trying to guess a password?** | Audit log → *Signed in* / *Sign-in failed* (the account tried and the IP). Staff accounts and the customer's Portal logins show who is locked out right now. |
| **Did an email fail to go?** | The bell tells the person whose action sent it; the admins see the rest. |
| **An error on a page or in the app?** | **Sentry** (error monitoring — `SENTRY_DSN`): the error, the page, when, how often. The phone app's own errors: the Admin's bell ("new errors in the phone app") opens their list. |
| **Is the site down or slow?** | The uptime check's alert; Render's dashboard (the service's logs, CPU, memory); Sentry's performance view. |

## 2. Contain it

- **An account misused or a password leaked:** Staff accounts (or the customer's Portal logins) → **Disable login**, and **Sign out of all devices**. Then the Admin sets a temporary password, or sends a setup link to the person's *own* email once it's safe.
- **Someone left the company:** deactivate them from their page (their open tasks move to a colleague).
- **A customer email went to the wrong person:** check the branch's contact email (Audit log shows who changed it); correct it; tell the customer.
- **Something keeps failing for everyone:** if a release caused it, roll back on Render (the previous deploy) — see §5.

## 3. Put it right

- **A wrong edit** (a customer's details, a branch, a setting): the Audit log has the value *before*; type it back on the record's page. The correction is itself recorded.
- **A task closed or cancelled by mistake:** the Admin can **reopen** a closed task; a cancelled one is re-created (its history stays).
- **A wrong report:** a manager corrects it from the task page (*Correct the report*); it's recorded, and the customer's copy can be sent again.
- **Stock that's wrong:** record a **count** on the Stock screen (what's on the shelf); the difference is kept, with who and why.
- **Data lost or damaged at scale:** restore from the database backup — to the minute before it happened where the hosting plan offers point-in-time recovery, else the last daily backup — into a *new* database, check it, then switch. Uploaded files (photos, reports) are restored from their own backup. **Never restore over the live database before checking the copy.**

## 4. Tell

- **Personal data exposed** (customers' or staff's details seen by someone who shouldn't): the Admin informs the management **the same day**; the UAE and Saudi data-protection laws expect the people affected and the authority to be told without undue delay (see the privacy items in `docs/go_live_plan.md`).
- **Customers affected by an outage or a wrong email:** a short, honest message from Customer Service.

## 5. Undo a release

- Render keeps every deploy: **Manual Deploy → the previous one** brings the last good version back in a minute.
- A release that changed the database (a migration) is rolled back only after checking that migration can be reversed; otherwise fix forward with a new release.

## 6. Learn

After anything serious: what happened, when it was noticed, what was done, and what changes so it doesn't happen again (a test, a check, a permission). Keep these notes with this runbook.

---

**What the system keeps, and for how long:** the Audit log two years (`manage.py prune_audit --keep-days 730`, monthly, in the scheduled job); task history and reports for as long as the machine is serviced; database backups as Render's plan keeps them (at least 30 days — `docs/go_live_plan.md`).
