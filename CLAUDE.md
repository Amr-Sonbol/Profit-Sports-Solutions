# Profit Sports Solutions — Field Service App

Django + PostgreSQL app for Profit Sports Solutions, a gym equipment agent
operating across the Middle East. Field technicians, service requests, and
equipment tracking are the core of the system.

The company name is always displayed in English, regardless of the active
language — it is never wrapped in `gettext`/`{% translate %}`.

One developer maintains this project. Keep things simple and readable over
clever or "enterprise" — prefer Django's built-in patterns, avoid unnecessary
abstraction layers, and don't add configurability nobody asked for.

## Database

The schema is specified in `docs/database_design_v2.md`. Read it before
writing models or migrations, and keep it in sync when the schema changes —
it's the source of truth, not the models themselves.

## Internationalization (Arabic + English)

The app is bilingual from the start — not an afterthought bolted on later.

- Wrap all user-facing strings in `gettext`/`gettext_lazy` (Django's i18n),
  from the first model and template written.
- Arabic is RTL. Every template and stylesheet must work in both directions.
- **Use logical CSS properties, never physical ones.** This is what makes RTL
  work without a separate stylesheet:
  - `padding-inline-start` / `padding-inline-end` — never `padding-left` / `padding-right`
  - `margin-inline-start` / `margin-inline-end` — never `margin-left` / `margin-right`
  - `inset-inline-start` / `inset-inline-end` — never `left` / `right`
  - `border-inline-start` / `border-inline-end` — never `border-left` / `border-right`
  - `text-align: start` / `end` — never `text-align: left` / `right`
  - Use `flex-direction: row` (it flips automatically with `dir`) — don't
    hardcode row-reverse for RTL.
- Set `dir` and `lang` on `<html>` based on the active language.
- Any new physical-property CSS in a code review is a bug, not a style nit.

## Timestamps

**All timestamps are stored in UTC.** Convert to the user's local timezone
only at display time (templates/serializers), never at the database or model
layer.

- `USE_TZ = True` in settings — leave it on.
- Use `django.utils.timezone.now()`, never `datetime.now()` or `datetime.utcnow()`.
- `DateTimeField`s store UTC; do the timezone conversion in the view/template
  layer using the request's locale/timezone, not by shifting stored values.

## General conventions

- Keep migrations small and one-purpose; don't bundle schema changes with
  data backfills unless necessary.
- Favor Django's built-in auth, admin, and forms over third-party
  replacements unless there's a clear, specific need.
- No speculative abstractions — this app has one developer and one deploy
  target; build for what's actually needed now.
