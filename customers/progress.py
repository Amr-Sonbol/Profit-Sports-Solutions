"""What a customer sees of their tickets' and visits' progress — in their
words, not the team's: Received → Being arranged → Visit booked → Done.
The portal home, a ticket's page and its visits all use these, so the
same thing never reads differently on two screens. Each is (label,
badge class).
"""
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from tasks.models import CustomerTicket, Task

DONE_STATUSES = (Task.Status.PENDING_SUPERVISOR_REVIEW, Task.Status.COMPLETED, Task.Status.CLOSED)

RECEIVED = (_('Received'), 'badge--neutral')
BEING_ARRANGED = (_('Being arranged'), 'badge--neutral')
VISIT_BOOKED = (_('Visit booked'), 'badge--booked')
ON_SITE = (_('Technician on site'), 'badge--status-in_progress')
DONE = (_('Done'), 'badge--good')
RESOLVED = (_('Resolved'), 'badge--good')
CLOSED = (_('Closed'), 'badge--muted')
CANCELLED = (_('Cancelled'), 'badge--muted')


def visit_day(task):
    """The day a visit happens, in the customer's own time (the middleware
    activates their country's timezone); None without one."""
    return timezone.localtime(task.scheduled_for).date() if task.scheduled_for else task.scheduled_date


def visit_progress(task):
    """A visit (task) as the customer sees it. A booked day that has
    passed with the work not done is being arranged again, not booked."""
    if task.status == Task.Status.CANCELLED:
        return CANCELLED
    if task.status in DONE_STATUSES:
        return DONE
    if task.status == Task.Status.IN_PROGRESS:
        return ON_SITE
    day = visit_day(task)
    if day and day >= timezone.localdate():
        return VISIT_BOOKED
    return BEING_ARRANGED


def ticket_progress(ticket):
    """A ticket as the customer sees it: once it became a visit, that
    visit's progress — "Scheduled" with no date yet was misleading."""
    if ticket.status == CustomerTicket.Status.NEW:
        return RECEIVED
    if ticket.status == CustomerTicket.Status.CONVERTED:
        return visit_progress(ticket.task) if ticket.task_id else BEING_ARRANGED
    if ticket.status == CustomerTicket.Status.CLOSED:
        return RESOLVED
    return CLOSED


def is_upcoming(task):
    """Still to happen: neither done nor cancelled."""
    return task.status not in DONE_STATUSES and task.status != Task.Status.CANCELLED


# The progress bar's steps, and where each progress word sits on it. Closed
# and cancelled aren't on the way to a visit, so they have no bar.
STEPS = (_('Received'), _('Being arranged'), _('Visit booked'), _('Done'))
# Pairs, not a dict: the lazy labels' hashes change with the language.
STEP_OF = ((RECEIVED, 0), (BEING_ARRANGED, 1), (VISIT_BOOKED, 2), (ON_SITE, 2), (DONE, 3), (RESOLVED, 3))


def progress_steps(progress):
    """The bar for a progress word: [(label, state)] with state 'done',
    'current' or 'todo' — or None when it isn't on the way to a visit."""
    current = next((index for step, index in STEP_OF if step is progress), None)
    if current is None:
        return None
    return [
        (label, 'done' if index < current or current == len(STEPS) - 1 else 'current' if index == current else 'todo')
        for index, label in enumerate(STEPS)
    ]
