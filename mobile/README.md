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
- Profile (`src/app/(app)/profile.tsx`) — name, role, country, sign out.

## What's here but read-only (supervisors)

- Team (`src/app/(app)/team.tsx`) — every open task in the supervisor's
  country. Not tappable into a detail view — the API's only task-detail
  endpoint (`/api/my-tasks/:id/`) is scoped to a technician's own
  assignment, so there's nothing to show yet for a task that isn't theirs.
- Tickets (`src/app/(app)/tickets.tsx`) — new customer tickets. Same
  limitation: viewing only, no assign/convert/dismiss action exists in the
  API yet.

Both show a plain message instead of a list for anyone without the
underlying web permission (`view_tasks` / `manage_tickets`) — nothing is
hidden by role name, since permissions are configurable per role on the
web side (Roles & permissions screen).

## What isn't built yet

Real supervisor use needs new endpoints on the Django side first — none
of this exists in `api/views.py` yet:

- View any task's full detail (not just one's own assignment)
- Assign a ticket or task to a technician
- Convert a ticket to a task
- Approve/review a filed work report

Also not built on the technician side: filing the work report itself
(parts used, labor hours, signature) once a task reaches that point —
`can_file_report` comes back from the API already, the screen for it
doesn't exist yet.

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
