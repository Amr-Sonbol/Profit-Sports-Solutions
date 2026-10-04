# Profit Sports Solutions — Mobile

A native app for technicians and supervisors, built with Expo/React Native.
Same accounts and permissions as the web app — this talks to the same
Django backend (`../api/`), it's just a different client.

## Setup

```bash
npm install
cp .env.example .env   # then edit EXPO_PUBLIC_API_BASE_URL — see the comments in it
npx expo start
```

From there, press `a` for Android, `i` for iOS (simulator, macOS only), or
scan the QR code with Expo Go on a physical device. The Django backend
(`docker compose up` from the repo root) needs to already be running.

## What's here (technician flow — fully working)

- Sign in (`src/app/sign-in.tsx`) — same accounts as the web login, token
  persisted with `expo-secure-store`.
- My Tasks (`src/app/(app)/index.tsx`) — every task assigned to the signed-in
  technician.
- Task detail (`src/app/(app)/task/[id].tsx`) — status, description, the
  one next action button (accept → en route → arrived → start), and adding
  a fault/serial-plate/before/after photo.
- Work report (`src/app/(app)/report/[id].tsx`) — findings, action taken,
  resolved, labour hours, parts used, and the customer signing on screen
  (`react-native-signature-canvas`). Same rules as the web form — the API
  reuses its save logic (`tasks.views.save_work_report`).
- Profile (`src/app/(app)/profile.tsx`) — name, role, country, sign out.

## Working with no signal

`src/offline.ts` keeps the last-loaded copy of My Tasks, each task, its report and the
parts list on the phone, so a technician who opened them earlier can still fill in a
report in a basement gym. Submitting with no connection saves the report on the phone;
My Tasks sends it automatically the next time it loads (or on pull-to-refresh) and shows
it as "waiting to send" until then. If the server refuses it (say a part code that's
since been switched off), it stays there marked "not sent — tap to fix". Status
buttons (accept, on my way, ...) still need a connection.

## Push notifications

`src/push.ts` registers the phone with the API after sign-in (and drops it on
sign-out); the server (`people/push.py`) sends through Expo's push service when
someone is assigned to a task, a report is waiting on its supervisor, a ticket is
escalated to someone, or a task message names someone. Tapping one opens that task.

**It needs the app linked to a free Expo account first** — until then (and in a
simulator) it quietly does nothing:

```bash
npx eas-cli@latest login      # your expo.dev account
npx eas-cli@latest init       # adds extra.eas.projectId to app.json
```

Push only works in a real build (`eas build`), not in Expo Go on Android.

## Supervisors, managers and admins

- Team (`src/app/(app)/team.tsx`) — every open task in the active
  country; tap one for its detail (`src/app/(app)/team-task/[id].tsx`):
  the team, the filed report, and the actions the server says this person
  may take — assign or change the lead, add or remove helpers (with a
  reason, same as the web), and approve the report (supervisor sign-off,
  or a manager's approve-and-close). Same rules as the web: country
  scope, the responsible-supervisor lock, no team changes once work has
  started.
- Tickets (`src/app/(app)/tickets.tsx`) — new customer tickets, read-only.
  Converting a ticket into a task means matching it to a customer and site,
  which stays on the web ticket desk.

Both show a plain message instead of a list for anyone without the
underlying web permission (`view_tasks` / `manage_tickets`) — nothing is
hidden by role name, since permissions are configurable per role on the
web side (Roles & permissions screen).

## Talking to the API

`src/api/client.ts` is the one place that attaches the auth token and
turns a non-2xx response into a thrown `ApiRequestError`. `src/api/
endpoints.ts` has one typed function per endpoint — add new ones there,
matching `api/serializers.py`/`api/views.py` on the Django side by hand
(there's no shared schema).

## Everything else (Expo/React Native mechanics)

See `AGENTS.md` in this directory — it has the current Expo Router
patterns, EAS build/submit commands, and the rule about checking versioned
docs instead of trusting training data on fast-moving Expo/RN APIs.
