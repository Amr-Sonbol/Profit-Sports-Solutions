"""Every action on a task tells the people on it.

Whatever records an action (a TaskEvent: the time moved, a step on site, the
report submitted or approved, blocked, cancelled, reopened…) also tells
everyone on the task — the active lead and helpers and the responsible
supervisor — except whoever did it: a bell entry each (TaskEventRecipient,
until they open the task) and a phone notification. One place, so an action
added later is told too. Connected in tasks.apps.TasksConfig.ready.
"""
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from people.push import push_in_their_language

# Told by their own, more specific notification already (people.push): a bell entry only.
PUSHED_ELSEWHERE = {'assigned', 'reassigned', 'cancelled'}
# Not about the team's work: no one is told.
NOT_TOLD = {'negligence'}


def people_on(task):
    """The active lead and helpers, and the responsible supervisor."""
    from people.models import Technician

    people = set(Technician.objects.filter(assignments__task=task, assignments__is_active=True, is_active=True))
    if task.responsible_supervisor_id and task.responsible_supervisor.is_active:
        people.add(task.responsible_supervisor)
    return people


def describe(event):
    """What happened, in the reader's language: "Rescheduled · Thu 12 Nov, 09:00"."""
    label = str(event.get_event_type_display())
    task = event.task
    if event.event_type == 'parts_arrived':
        from tasks.parts_needed import is_shipment
        if is_shipment(task):
            return _('All parts are in — prepare and ship')
        if task.scheduled_for or task.scheduled_date:
            day = timezone.localtime(task.scheduled_for).date() if task.scheduled_for else task.scheduled_date
            return _('All parts are in for the visit on %(date)s') % {'date': f'{day:%d %b}'}
        return _('All parts are in — schedule the visit')
    if event.event_type == 'rescheduled' and task.scheduled_for:
        return f'{label} · {timezone.localtime(task.scheduled_for):%a %d %b, %H:%M}'
    return label


def tell(event):
    from tasks.models import TaskEventRecipient

    if event.event_type in NOT_TOLD:
        return
    task = event.task
    actor = getattr(event.actor, 'technician', None) if event.actor_id else None
    people = people_on(task)
    if event.event_type == 'parts_arrived':
        # The warehouse prepares them next (or ships them): its manager in the task's country.
        from people.models import Technician
        people |= set(Technician.objects.filter(
            role=Technician.Role.WAREHOUSE_MANAGER, country_id=task.site.customer.country_id, is_active=True,
        ))
    # "All parts are in" is a signal to act (schedule, prepare, ship): everyone it concerns, the one who saw it in too.
    recipients = [person for person in people if person != actor or event.event_type == 'parts_arrived']
    if not recipients:
        return
    TaskEventRecipient.objects.bulk_create([TaskEventRecipient(event=event, technician=person) for person in recipients])
    if event.event_type in PUSHED_ELSEWHERE:
        return
    who = getattr(actor, 'full_name', '') or (event.actor.get_username() if event.actor_id else '')
    for person in recipients:
        push_in_their_language(
            person,
            lambda: f'{task.task_number} · {describe(event)}',
            lambda: ' · '.join(part for part in (
                task.site.name, _('by %(name)s') % {'name': who} if who else '', (event.note or '')[:120],
            ) if part),
            {'type': 'task_update', 'task_id': task.pk},
        )


def on_event_saved(sender, instance, created, **kwargs):
    if created:
        # Once the action is saved for good: nobody hears of one that was rolled back.
        transaction.on_commit(lambda: tell(instance))
