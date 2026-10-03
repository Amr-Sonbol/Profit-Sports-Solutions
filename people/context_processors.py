from reference.models import Country

from .models import RolePermission
from .permissions import get_active_country


def notification_bell(request):
    """The header bell's contents — new tickets, new customer replies,
    newly created tasks nobody's looked at yet, and task messages
    addressed to this specific technician. The first two halves are a
    shared inbox, gated by capability and cleared for everyone the
    moment anyone with that same capability opens the thing: tickets on
    manage_tickets (today, only Admin and Technical Support Manager),
    tasks on is_manager_tier. Task messages are different — real
    individuals are named (see TaskMessageRecipient), so every technician
    checks their own row regardless of role, and it only clears once
    they personally open that task. Country-scoped like everything else
    here. Three different notification models, merged into one list
    here rather than in any one app's own views, since this is the only
    place that needs them together. Tasks still waiting for a date after
    24 working hours (tasks.alarms) show up here too — computed live, so
    they stay until the task is scheduled rather than until opened.
    """
    technician = getattr(request.user, 'technician', None)
    if technician is None:
        return {}
    can_manage_tickets = RolePermission.objects.filter(
        role=technician.role, permission=RolePermission.Permission.MANAGE_TICKETS, allowed=True,
    ).exists()
    from tasks.models import TaskMessageRecipient, TaskNotification, TicketNotification
    country = get_active_country(request)
    items = []
    if can_manage_tickets:
        unseen_tickets = TicketNotification.objects.filter(
            seen_at__isnull=True, ticket__country=country,
        ).select_related('ticket')
        items += [
            {'kind': notification.kind, 'created_at': notification.created_at, 'ticket': notification.ticket}
            for notification in unseen_tickets
        ]
    if technician.is_manager_tier:
        unseen_tasks = TaskNotification.objects.filter(
            seen_at__isnull=True, task__site__customer__country=country,
        ).select_related('task')
        items += [
            {'kind': 'new_task', 'created_at': notification.created_at, 'task': notification.task}
            for notification in unseen_tasks
        ]
    from tasks.models import TicketEscalation
    items += [
        {'kind': 'escalation_pending', 'created_at': escalation.escalated_at, 'ticket': escalation.ticket}
        for escalation in TicketEscalation.objects.filter(
            escalated_to=technician, decision=TicketEscalation.Decision.PENDING, ticket__country=country,
        ).select_related('ticket')
    ]
    from tasks.alarms import overdue_unscheduled_tasks
    items += [
        {'kind': 'unscheduled_overdue', 'created_at': task.created_at, 'task': task}
        for task in overdue_unscheduled_tasks(country, technician)
    ]
    unseen_messages = TaskMessageRecipient.objects.filter(
        technician=technician, seen_at__isnull=True, message__task__site__customer__country=country,
    ).select_related('message__task')
    items += [
        {'kind': 'task_message', 'created_at': recipient.message.sent_at, 'task': recipient.message.task}
        for recipient in unseen_messages
    ]
    # Whoever holds the shared inbox (tickets or tasks) always gets a
    # count, even zero — same as before task messages existed. Anyone
    # else (a plain technician, say) only gets bell context at all once
    # they actually have a message addressed to them.
    if not items and not (can_manage_tickets or technician.is_manager_tier):
        return {}
    items.sort(key=lambda item: item['created_at'], reverse=True)
    # Overdue-scheduling alarms are the oldest items by nature — kept on
    # top so they're never pushed out of the ten shown by newer ones.
    items.sort(key=lambda item: item['kind'] != 'unscheduled_overdue')
    return {
        'unseen_notifications': items[:10],
        'unseen_notification_count': len(items),
    }


def role_permissions(request):
    """The signed-in technician's currently-allowed permission codes, for
    templates — so nav links follow the same configurable rules the views
    themselves enforce, instead of a second, separately-hardcoded set of
    role checks that could drift out of sync with it.
    """
    technician = getattr(request.user, 'technician', None)
    if technician is None:
        return {}
    allowed = set(
        RolePermission.objects.filter(role=technician.role, allowed=True).values_list('permission', flat=True),
    )
    return {'role_permissions': allowed}


def active_country(request):
    """Which country every screen is currently scoped to, and — for the
    manager tier (manager or admin, matching get_active_country's own
    is_manager_tier check) — the full list to switch between in the
    header. Empty for everyone else, so the switcher template block
    simply doesn't render rather than showing a single fixed,
    unchangeable option.
    """
    technician = getattr(request.user, 'technician', None)
    if technician is None:
        return {}
    context = {'active_country': get_active_country(request)}
    if technician.is_manager_tier:
        context['switchable_countries'] = Country.objects.filter(is_active=True).order_by('name')
    return context
