from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from reference.models import Country

from .models import RolePermission, Technician
from .permissions import get_active_country


def notification_bell(request):
    """The header bell's contents — new tickets, new customer replies,
    newly created tasks nobody's looked at yet, and task messages
    addressed to this specific technician. The first two halves are a
    shared inbox, gated by capability and cleared for everyone the
    moment anyone with that same capability opens the thing: tickets on
    manage_tickets (today, Technical Support Manager, Operations Manager
    and Admin), tasks on is_manager_tier. Task messages are different — real
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
        unseen_tickets = TicketNotification.objects.filter(seen_at__isnull=True).select_related('ticket__country')
        # The company-wide roles hear of every country's tickets, whichever country they're looking at.
        if not RolePermission.objects.filter(
            role=technician.role, permission=RolePermission.Permission.TICKETS_ALL_COUNTRIES, allowed=True,
        ).exists():
            unseen_tickets = unseen_tickets.filter(ticket__country=country)
        # A new ticket: only to whoever is told of it, as by its email (tasks.alarms.new_ticket_people —
        # the chosen roles, or the admins when nobody of those reaches its country);
        # replies and escalation decisions: to everyone who handles tickets.
        from tasks.alarms import new_ticket_people
        told_in = {}
        for notification in unseen_tickets:
            ticket_country = notification.ticket.country
            if notification.kind == TicketNotification.Kind.NEW_TICKET and ticket_country.pk not in told_in:
                told_in[ticket_country.pk] = new_ticket_people(ticket_country).filter(pk=technician.pk).exists()
        unseen_tickets = [
            notification for notification in unseen_tickets
            if notification.kind != TicketNotification.Kind.NEW_TICKET or told_in[notification.ticket.country_id]
        ]
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
    if technician.is_manager_tier:
        # A visit's time a supervisor asks to move — live, for whoever can approve it (the manager
        # tier, in that country), until it's approved or refused.
        from tasks.models import ScheduleChangeRequest
        items += [
            {'kind': 'schedule_change_pending', 'created_at': change.created_at, 'task': change.task}
            for change in ScheduleChangeRequest.objects.filter(
                status=ScheduleChangeRequest.Status.PENDING, task__site__customer__country=country,
            ).select_related('task__site')
        ]
    from tasks.models import TicketEscalation
    items += [
        {'kind': 'escalation_pending', 'created_at': escalation.escalated_at, 'ticket': escalation.ticket}
        for escalation in TicketEscalation.objects.filter(
            escalated_to=technician, decision=TicketEscalation.Decision.PENDING,
        ).select_related('ticket')
    ]
    from tasks.alarms import overdue_unscheduled_tasks
    # One entry for them all, so a busy day of them never pushes newer news (a new ticket) out of the ten shown.
    overdue = overdue_unscheduled_tasks(country, technician)
    if len(overdue) == 1:
        items.append({'kind': 'unscheduled_overdue', 'created_at': overdue[0].created_at, 'task': overdue[0]})
    elif overdue:
        items.append({'kind': 'unscheduled_overdue_many', 'created_at': overdue[0].created_at, 'count': len(overdue)})
    from tasks.alarms import late_reports
    late = late_reports(technician)
    if len(late) == 1:
        items.append({'kind': 'report_late', 'created_at': late[0].waiting_since, 'task': late[0]})
    elif late:
        items.append({'kind': 'report_late_many', 'created_at': late[0].waiting_since, 'count': len(late)})
    # Internal tickets (stock, vendor, other) waiting on this person — live,
    # so they stay until closed, like the overdue alarms above.
    from tasks.models import CustomerTicket
    items += [
        {'kind': 'internal_ticket', 'created_at': ticket.assigned_at or ticket.submitted_at, 'ticket': ticket}
        for ticket in CustomerTicket.objects.filter(
            assigned_to=technician, status=CustomerTicket.Status.NEW, country=country,
        ).exclude(kind=CustomerTicket.Kind.CUSTOMER)
    ]
    # Customer logins locked out right now — live, so they clear the moment
    # someone unlocks them (or the lockout times out on its own).
    if RolePermission.objects.filter(
        role=technician.role, permission=RolePermission.Permission.CUSTOMER_ACCOUNTS, allowed=True,
    ).exists():
        from django.db.models.functions import Lower

        from customers.accounts import locked_usernames
        from customers.models import Customer
        locked = locked_usernames()
        if locked:
            items += [
                {'kind': 'customer_locked', 'created_at': locked[customer.login], 'customer': customer}
                for customer in Customer.objects.annotate(login=Lower('user__username')).filter(
                    login__in=list(locked), is_active=True,
                )
            ]
    # The phone app's errors not looked at yet — the admins'; gone once the list is opened.
    if technician.role == Technician.Role.ADMIN:
        from people.models import AppError
        new_errors = AppError.objects.filter(seen_at__isnull=True)
        latest = new_errors.order_by('-occurred_at').values_list('occurred_at', flat=True).first()
        if latest:
            items.append({'kind': 'app_errors', 'created_at': latest, 'count': new_errors.count()})
    # Staff logins locked out right now, for the manager tier to unlock
    # from Staff accounts — live, like the customer lockouts above.
    if technician.is_manager_tier:
        from customers.accounts import locked_usernames
        locked = locked_usernames()
        if locked:
            from django.db.models.functions import Lower

            items += [
                {'kind': 'staff_locked', 'created_at': locked[person.login], 'person': person}
                for person in Technician.objects.annotate(login=Lower('user__username')).filter(
                    login__in=list(locked), is_active=True,
                )
            ]
    # What happened to tasks they're on (tasks.updates): theirs until they open the task.
    from tasks.models import TaskEventRecipient
    from tasks.updates import describe
    unseen_updates = TaskEventRecipient.objects.filter(
        technician=technician, seen_at__isnull=True, event__task__site__customer__country=country,
    ).select_related('event__task__site', 'event__actor__technician')
    items += [
        {
            'kind': 'task_update', 'created_at': recipient.event.occurred_at, 'task': recipient.event.task,
            'what': describe(recipient.event),
            'sender': getattr(getattr(recipient.event.actor, 'technician', None), 'full_name', '')
            if recipient.event.actor_id else '',
        }
        for recipient in unseen_updates
    ]
    unseen_messages = TaskMessageRecipient.objects.filter(
        technician=technician, seen_at__isnull=True, message__task__site__customer__country=country,
    ).select_related('message__task', 'message__sent_by__technician')
    items += [
        {
            'kind': 'task_message', 'created_at': recipient.message.sent_at, 'task': recipient.message.task,
            'sender': getattr(getattr(recipient.message.sent_by, 'technician', None), 'full_name', '')
            or recipient.message.sent_by.get_username(),
        }
        for recipient in unseen_messages
    ]
    # Whoever holds the shared inbox (tickets or tasks) always gets a
    # count, even zero — same as before task messages existed. Anyone
    # else (a plain technician, say) only gets bell context at all once
    # they actually have a message addressed to them.
    if not items and not (can_manage_tickets or technician.is_manager_tier):
        return {}
    items.sort(key=lambda item: item['created_at'], reverse=True)
    # The overdue-scheduling alarm is the oldest item by nature — kept on
    # top so it's never pushed out of the ten shown by newer ones.
    items.sort(key=lambda item: not item['kind'].startswith(('unscheduled_overdue', 'report_late')))
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
    pages = nav_pages(technician, allowed)
    # My profile is the row at the top of the sidebar, not a menu page; on
    # it, that row lights up rather than whichever page shares its /tasks/.
    on_profile = request.path.startswith(reverse('tasks:my_profile'))
    return {
        'role_permissions': allowed, 'nav_pages': pages,
        'nav_groups': nav_groups(pages, '' if on_profile else request.path), 'profile_active': on_profile,
    }


# Sidebar sections, in order. A heading of None is a section without a
# title (your own pages at the top, setup pages at the bottom), set off by
# a divider instead.
NAV_GROUPS = [
    ('mine', None),
    ('tasks', _('Tasks')),
    ('team', _('Team')),
    ('customers', _('Customers')),
    ('setup', None),
]


def nav_pages(technician, allowed):
    """Every page this technician can open, in header-menu order, as
    (url, label, icon, group) dicts — the one list both the sidebar and
    the welcome page's icon tiles are drawn from, so the two can't disagree.
    Each gate here mirrors the check the page's own view enforces
    (require_permission / require_technician / require_manager /
    require_admin); `icon` names a symbol in templates/_icon.html, `group`
    a NAV_GROUPS key.
    """
    pages = []

    def add(url_name, label, icon, group):
        pages.append({'url': reverse(url_name), 'label': label, 'icon': icon, 'group': group})

    # Everyone has their own schedule (a manager's: tasks they're on or
    # responsible for); progress and skills are the field team's.
    add('tasks:my_week', _('My schedule'), 'calendar', 'mine')
    if technician.is_field_staff:
        add('tasks:my_progress', _('My progress'), 'trending-up', 'mine')
        add('tasks:my_skills', _('My skills'), 'award', 'mine')
    if RolePermission.Permission.VIEW_DASHBOARD in allowed:
        add('tasks:dashboard', _('Dashboard'), 'grid', 'tasks')
    if RolePermission.Permission.VIEW_TASKS in allowed:
        add('tasks:task_list', _('Tasks'), 'clipboard', 'tasks')
    if RolePermission.Permission.VIEW_TECHNICIANS in allowed:
        # Named for who's in it (tasks.views.overseen_roles).
        if technician.role == technician.Role.ADMIN:
            add('tasks:technician_list', _('Users'), 'users', 'team')
        elif technician.is_any_manager:
            add('tasks:technician_list', _('My team'), 'users', 'team')
        else:
            add('tasks:technician_list', _('Technicians'), 'users', 'team')
        add('tasks:team_progress', _('Team progress'), 'bar-chart', 'team')
        add('tasks:technician_hours', _('Hours'), 'clock', 'team')
        add('tasks:team_map', _('Team map'), 'map-pin', 'team')
    if RolePermission.Permission.MANAGE_CUSTOMERS in allowed:
        add('customers:customer_list', _('Customers'), 'building', 'customers')
    if RolePermission.Permission.MANAGE_PARTS in allowed:
        add('tasks:part_list', _('Parts'), 'package', 'customers')
    if RolePermission.Permission.VIEW_STOCK in allowed:
        add('tasks:stock', _('Stock'), 'archive', 'customers')
    if RolePermission.Permission.VIEW_MACHINES in allowed:
        add('tasks:machine_list', _('Machines'), 'dumbbell', 'customers')
    if RolePermission.Permission.MANAGE_TICKETS in allowed:
        add('tasks:ticket_list', _('Tickets'), 'ticket', 'tasks')
    if RolePermission.Permission.OPEN_TICKETS in allowed:
        add('tasks:ticket_open', _('New ticket'), 'ticket-plus', 'tasks')
    # Anyone in the office can be handed — and create — an internal task
    # (a CustomerTicket of kind internal).
    if technician.role != technician.Role.TECHNICIAN:
        add('tasks:internal_tickets', _('Internal tasks'), 'inbox', 'tasks')
        add('tasks:internal_ticket_new', _('New internal task'), 'ticket-plus', 'tasks')
    if technician.reaches_all_countries:
        add('tasks:all_week', _('All countries — schedule'), 'calendar-range', 'tasks')
        add('tasks:all_technicians', _('All technicians'), 'id-card', 'team')
    if technician.is_manager_tier:
        add('tasks:staff_accounts', _('Staff accounts'), 'key', 'team')
    # Company-wide (every country): not a Manager's, who has one (docs/access_rules.md).
    if technician.reaches_all_countries:
        add('tasks:monthly_report', _('Monthly report'), 'bar-chart', 'team')
        add('tasks:skill_list', _('Skills'), 'graduation-cap', 'team')
        add('tasks:brand_list', _('Brands'), 'tag', 'setup')
        add('tasks:country_list', _('Countries'), 'globe', 'setup')
    if technician.role == technician.Role.ADMIN:
        add('tasks:role_permissions', _('Roles'), 'shield', 'setup')
        add('tasks:notification_settings', _('Notifications'), 'bell', 'setup')
        add('tasks:audit_log', _('Audit log'), 'file-clock', 'setup')
    return pages


def nav_groups(pages, path):
    """The same pages split into the sidebar's NAV_GROUPS sections (empty
    ones dropped), with the current page marked `active`: the page whose
    URL is the longest prefix of this path, so a task's own page still
    lights up Tasks and the dashboard isn't mistaken for it.
    """
    matches = [page['url'] for page in pages if path.startswith(page['url'])]
    current = max(matches, key=len) if matches else None
    groups = []
    for key, heading in NAV_GROUPS:
        group_pages = [dict(page, active=page['url'] == current) for page in pages if page['group'] == key]
        if group_pages:
            groups.append({'heading': heading, 'pages': group_pages})
    return groups


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
    if technician.reaches_all_countries:
        context['switchable_countries'] = Country.objects.filter(is_active=True).order_by('name')
    return context
