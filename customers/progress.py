"""What a customer sees of their tickets' and visits' progress — in their
words, not the team's: Received → Visit scheduled → On the way → On site →
Done. No "Being arranged": it read as already arranged; until there's a
date the request is simply received, and we're preparing it.
The portal home, a ticket's page and its visits all use these, so the
same thing never reads differently on two screens. Each is (label,
badge class).
"""
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _

from tasks.models import CustomerTicket, Task, TaskEvent

DONE_STATUSES = (Task.Status.PENDING_SUPERVISOR_REVIEW, Task.Status.COMPLETED, Task.Status.CLOSED)

RECEIVED = (_('Received'), 'badge--neutral')
# Accepted, no date yet (often waiting for parts): still "Received", saying what we're doing.
BEING_ARRANGED = (_("Received — we're preparing your visit and will confirm the date with you"), 'badge--neutral')
VISIT_BOOKED = (_('Visit scheduled'), 'badge--booked')
# A booked day that passed with the work not done: we owe them a new date.
RESCHEDULING = (_("Rescheduling — we'll contact you with a new date"), 'badge--warning')
WAITING_FOR_PARTS = (_("Waiting for parts — we'll schedule your visit as soon as they arrive"), 'badge--neutral')
ON_THE_WAY = (_('Technician on the way'), 'badge--status-in_progress')
ON_SITE = (_('Technician on site'), 'badge--status-in_progress')
DONE = (_('Done'), 'badge--good')
RESOLVED = (_('Resolved'), 'badge--good')
CLOSED = (_('Closed'), 'badge--muted')
CANCELLED = (_('Cancelled'), 'badge--muted')


def visit_day(task):
    """The day a visit happens, in the customer's own time (the middleware
    activates their country's timezone); None without one."""
    return timezone.localtime(task.scheduled_for).date() if task.scheduled_for else task.scheduled_date


def shown_day(task, progress):
    """The day the customer is shown for a visit: its day — also while it's under
    way, a job over several days included — except one that passed with the work
    not done (rescheduling: a new date is coming, not that one)."""
    return None if progress is RESCHEDULING else visit_day(task)


def visit_progress(task):
    """A visit (task) as the customer sees it. A booked day that has
    passed with the work not done is rescheduling, not booked."""
    if task.status == Task.Status.CANCELLED:
        return CANCELLED
    if task.status in DONE_STATUSES:
        return DONE
    if task.status == Task.Status.IN_PROGRESS:
        return ON_SITE
    # Today's taps: set off, or arrived (before the work starts).
    tapped = today_tap(task)
    if tapped == TaskEvent.EventType.ARRIVED:
        return ON_SITE
    if tapped == TaskEvent.EventType.EN_ROUTE:
        return ON_THE_WAY
    day = visit_day(task)
    if day and day >= timezone.localdate():
        # The day itself, in the customer's language, and the time when there is one
        # (their own timezone): "Visit scheduled on 12 November at 10 a.m.".
        date = date_format(day, 'j F')
        if task.scheduled_for:
            label = _('Visit scheduled on %(date)s at %(time)s') % {
                'date': date, 'time': date_format(timezone.localtime(task.scheduled_for), 'P'),
            }
        else:
            label = _('Visit scheduled on %(date)s') % {'date': date}
        return (label, VISIT_BOOKED[1])
    if day:
        return RESCHEDULING
    # No date yet: ordered parts not in yet say so (and when they should be).
    from tasks.parts_needed import waiting_for_parts
    waiting, expected = waiting_for_parts(task)
    if waiting:
        if expected:
            return (_('Waiting for parts — expected around %(date)s') % {'date': date_format(expected, 'j F')},
                    WAITING_FOR_PARTS[1])
        return WAITING_FOR_PARTS
    return BEING_ARRANGED


def today_tap(task):
    """The lead's latest "On the way" or "Arrived" today (an undone one is gone), or None."""
    if hasattr(task, '_today_tap'):  # read for a whole list at once (with_today_taps)
        return task._today_tap
    from zoneinfo import ZoneInfo
    # Their country's own day, not the server's (UTC).
    country_tz = ZoneInfo(task.site.customer.country.timezone)
    start = timezone.localtime(timezone.now(), country_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return task.events.filter(
        event_type__in=[TaskEvent.EventType.EN_ROUTE, TaskEvent.EventType.ARRIVED], occurred_at__gte=start,
    ).order_by('-occurred_at').values_list('event_type', flat=True).first()


def with_today_taps(tasks):
    """today_tap for a whole list of one customer's visits in a single query
    (not one per visit). Their country's day, as today_tap counts it."""
    from zoneinfo import ZoneInfo
    tasks = list(tasks)
    if not tasks:
        return tasks
    country_tz = ZoneInfo(tasks[0].site.customer.country.timezone)
    start = timezone.localtime(timezone.now(), country_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    latest = {}
    for task_id, event_type in TaskEvent.objects.filter(
        task__in=tasks, event_type__in=[TaskEvent.EventType.EN_ROUTE, TaskEvent.EventType.ARRIVED],
        occurred_at__gte=start,
    ).order_by('occurred_at').values_list('task_id', 'event_type'):
        latest[task_id] = event_type  # the last one wins
    for task in tasks:
        task._today_tap = latest.get(task.pk)
    return tasks


def is_booked(progress):
    """Whether a progress word is a scheduled visit (its label carries the day)."""
    return progress[1] == VISIT_BOOKED[1]


def ticket_progress(ticket):
    """A ticket as the customer sees it: once it became a visit, that
    visit's progress — "Scheduled" with no date yet was misleading."""
    if ticket.status == CustomerTicket.Status.NEW:
        return RECEIVED
    if ticket.status == CustomerTicket.Status.CONVERTED:
        # Split over several visits (one per machine, say): the least advanced one — a request
        # isn't "Done" while one of its visits is still to come. A machine still waiting: arranging.
        visits = {machine.task for machine in ticket.machines.all() if machine.task_id}
        if ticket.task_id:
            visits.add(ticket.task)
        visits = [visit for visit in visits if visit.status != Task.Status.CANCELLED]
        if not visits or any(not machine.decision for machine in ticket.machines.all()):
            return BEING_ARRANGED
        return min((visit_progress(visit) for visit in visits), key=_step_index)
    if ticket.status == CustomerTicket.Status.CLOSED:
        return RESOLVED
    return CLOSED


def is_upcoming(task):
    """Still to happen: neither done nor cancelled."""
    return task.status not in DONE_STATUSES and task.status != Task.Status.CANCELLED


# The progress bar's steps, and where each progress word sits on it. Closed
# and cancelled aren't on the way to a visit, so they have no bar.
STEPS = (_('Received'), _('Visit scheduled'), _('On the way'), _('On site'), _('Done'))
# Pairs, not a dict: the lazy labels' hashes change with the language.
STEP_OF = (
    (RECEIVED, 0), (BEING_ARRANGED, 0), (WAITING_FOR_PARTS, 0), (RESCHEDULING, 0), (VISIT_BOOKED, 1), (ON_THE_WAY, 2), (ON_SITE, 3),
    (DONE, 4), (RESOLVED, 4),
)


def _step_index(progress):
    """How far a progress word is along the way to a visit (STEP_OF; booked is 1)."""
    if is_booked(progress):
        return 1
    return next((index for step, index in STEP_OF if step is progress), 0)


def progress_steps(progress):
    """The bar for a progress word: [(label, state)] with state 'done',
    'current' or 'todo' — or None when it isn't on the way to a visit."""
    if is_booked(progress):
        current = 1
    elif progress[1] == WAITING_FOR_PARTS[1] and progress not in (CLOSED, CANCELLED):
        current = 0  # received, being prepared or waiting for parts (its words may carry a date)
    else:
        current = next((index for step, index in STEP_OF if step is progress), None)
    if current is None:
        return None
    return [
        (label, 'done' if index < current or current == len(STEPS) - 1 else 'current' if index == current else 'todo')
        for index, label in enumerate(STEPS)
    ]
