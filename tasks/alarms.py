"""The "not scheduled within 24 working hours" alarm — worked out live
when the header bell renders, not by a background job, so it needs no
scheduler and clears itself the moment the task gets a date.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Min, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from people.models import Technician

from .models import Task, TaskEvent

UNSCHEDULED_ALARM_HOURS = 24

# Still waiting on someone to pick a day — once work has started, or the
# task is blocked/finished, a missing date isn't what's holding it up.
AWAITING_SCHEDULE_STATUSES = [Task.Status.NEW, Task.Status.ASSIGNED, Task.Status.ACCEPTED]


def working_hours_between(start, end, tz, weekend_weekdays):
    """Hours from start to end, leaving out whole weekend days as they
    fall in the country's own timezone.
    """
    start = start.astimezone(tz)
    end = end.astimezone(tz)
    total = timedelta()
    day = start.date()
    while day <= end.date():
        if day.weekday() not in weekend_weekdays:
            day_start = datetime.combine(day, time.min, tzinfo=tz)
            day_end = day_start + timedelta(days=1)
            overlap = min(end, day_end) - max(start, day_start)
            if overlap > timedelta():
                total += overlap
        day += timedelta(days=1)
    return total.total_seconds() / 3600


def overdue_unscheduled_tasks(country, technician, now=None, hours=UNSCHEDULED_ALARM_HOURS):
    """Open tasks in this country with no date yet, more than 24 working
    hours after they were created — every hour counts for an emergency,
    since the team works weekends for those. A supervisor sees the ones they're
    responsible for, plus any nobody is responsible for; the manager tier
    sees all of them. Everyone else, nothing.
    """
    if technician.role == Technician.Role.SUPERVISOR:
        scope = Q(responsible_supervisor=technician) | Q(responsible_supervisor__isnull=True)
    elif technician.is_manager_tier:
        scope = Q()
    else:
        return []

    now = now or timezone.now()
    tasks = Task.objects.filter(
        scope, site__customer__country=country, status__in=AWAITING_SCHEDULE_STATUSES,
        scheduled_for__isnull=True, scheduled_date__isnull=True,
    ).annotate(
        # The created event is when it really entered the system;
        # reported_at (typed in, can be backdated) only for old tasks
        # from before that event existed.
        created_at=Coalesce(
            Min('events__occurred_at', filter=Q(events__event_type=TaskEvent.EventType.CREATED)), 'reported_at',
        ),
    ).select_related('site')

    tz = ZoneInfo(country.timezone)
    weekend = country.weekend_weekdays
    return [
        task for task in tasks
        if working_hours_between(
            task.created_at, now, tz, set() if task.priority == Task.Priority.EMERGENCY else weekend,
        ) > hours
    ]


LATE_REPORT_HOURS = 48  # 2 working days


def late_reports(technician, now=None):
    """Reports waiting more than 2 working days (each country's weekend left
    out) for the Technical Support Manager's final approval — the Operations
    Manager's safety net (docs/access_rules.md); nobody else's. Oldest first,
    each with `waiting_since` and `waiting_days`. Live: gone once approved.
    """
    if technician.role != Technician.Role.OPERATIONS_MANAGER:
        return []
    return _late_reports(now)


def _late_reports(now=None):
    """late_reports' search itself: every report waiting more than 2 working days."""
    from django.db.models import Max

    now = now or timezone.now()
    late = []
    for task in Task.objects.filter(status=Task.Status.COMPLETED).annotate(
        # Since it reached the final approval: the supervisor's review, the report itself, or a reopen.
        waiting_since=Max('events__occurred_at', filter=Q(events__event_type__in=[
            TaskEvent.EventType.SUPERVISOR_APPROVED, TaskEvent.EventType.COMPLETED, TaskEvent.EventType.REOPENED,
        ])),
    ).filter(waiting_since__isnull=False).select_related('site__customer__country'):
        country = task.site.customer.country
        span = now - task.waiting_since
        if span < timedelta(hours=LATE_REPORT_HOURS):
            continue  # can't be 2 working days yet
        if span > timedelta(days=14):
            # Certainly late; counted day by day only when it's close — the bell renders this on every page.
            hours = span.total_seconds() / 3600 * (7 - len(country.weekend_weekdays)) / 7
        else:
            hours = working_hours_between(task.waiting_since, now, ZoneInfo(country.timezone), country.weekend_weekdays)
        if hours > LATE_REPORT_HOURS:
            task.waiting_days = int(hours // 24)
            late.append(task)
    late.sort(key=lambda task: task.waiting_since)
    return late


def new_ticket_people(country):
    """Who is told of a new ticket in this country, by email and in the bell:
    the roles chosen on the Notifications page — in this country, or any
    country for a role that handles tickets in all of them (the Technical
    Support Manager). Only someone whose own switches handle tickets — a
    custom role based on a chosen one but with Tickets off is not told.
    Nobody of those, active with a login: the admins, so a ticket is never
    left unseen."""
    from people.models import NotificationSettings, RolePermission, with_permission

    roles = NotificationSettings.load().new_ticket_roles
    people = Technician.objects.filter(
        Q(country=country) | with_permission(RolePermission.Permission.TICKETS_ALL_COUNTRIES),
        with_permission(RolePermission.Permission.MANAGE_TICKETS),
        is_active=True, role__in=roles, user__isnull=False,
    )
    if not people.exists():
        people = Technician.objects.filter(role=Technician.Role.ADMIN, is_active=True, user__isnull=False)
    return people


# Who keeps an eye on the whole company (docs/access_rules.md). The urgent
# alarms of every country (a task still blocked after 2 working hours, one with
# no day after 24) reach the Technical Support Manager and the Operations
# Manager, whichever country they're looking at; the full summary at the top of
# the Dashboard (company_summary) is the Technical Support Manager's alone.
COMPANY_WATCH_ROLES = (Technician.Role.SUPPORT_MANAGER, Technician.Role.OPERATIONS_MANAGER)
COMPANY_SUMMARY_ROLES = (Technician.Role.SUPPORT_MANAGER,)
# Each level up hears later, of what the level below didn't solve: (blocked, no day), in working hours.
# The Operations Manager only of what stayed stuck after the Technical Support Manager knew.
ALARM_HOURS = {
    Technician.Role.SUPPORT_MANAGER: (2, UNSCHEDULED_ALARM_HOURS),
    Technician.Role.OPERATIONS_MANAGER: (2 * 24, 3 * 24),  # 2 working days; 3 working days
}


def watches_company(technician):
    return technician is not None and technician.role in COMPANY_WATCH_ROLES


def company_unscheduled_tasks(technician, now=None):
    """overdue_unscheduled_tasks in every active country, for whoever watches the company."""
    from reference.models import Country

    if not watches_company(technician):
        return []
    now = now or timezone.now()
    hours = ALARM_HOURS[technician.role][1]
    return [task for country in Country.objects.filter(is_active=True)
            for task in overdue_unscheduled_tasks(country, technician, now, hours)]


def long_blocked_tasks(technician, now=None):
    """Tasks blocked on site for longer than this person's ALARM_HOURS (working
    hours, their country's weekend left out), in every country — for whoever
    watches the company; their supervisor already heard the moment it happened
    (tasks.updates). Each with `blocked_since`. Live: gone once unblocked."""
    from django.db.models import Max

    if not watches_company(technician):
        return []
    now = now or timezone.now()
    blocked = Task.objects.filter(status=Task.Status.BLOCKED).annotate(
        blocked_since=Max('events__occurred_at', filter=Q(events__event_type=TaskEvent.EventType.BLOCKED)),
    ).filter(blocked_since__isnull=False).select_related('site__customer__country')
    late = []
    for task in blocked:
        country = task.site.customer.country
        if working_hours_between(task.blocked_since, now, ZoneInfo(country.timezone),
                                 country.weekend_weekdays) > ALARM_HOURS[technician.role][0]:
            late.append(task)
    return sorted(late, key=lambda task: task.blocked_since)


def company_summary(technician, now=None):
    """Everything that needs watching across the company, as counts — the top
    of the Dashboard for whoever watches it (the website's and the app's).
    None for anyone else. Live: each number is what's true right now."""
    from customers.accounts import locked_usernames
    from customers.models import PortalUser
    from django.db.models.functions import Lower
    from tasks.models import CustomerTicket, TicketEscalation

    if technician is None or technician.role not in COMPANY_SUMMARY_ROLES:
        return None
    now = now or timezone.now()
    open_requests = CustomerTicket.objects.filter(kind=CustomerTicket.Kind.CUSTOMER, status=CustomerTicket.Status.NEW)
    locked = list(locked_usernames())
    return {
        'blocked': Task.objects.filter(status=Task.Status.BLOCKED).count(),
        'blocked_long': len(long_blocked_tasks(technician, now)),
        'unscheduled_overdue': len(company_unscheduled_tasks(technician, now)),
        'new_tasks_today': Task.objects.filter(
            events__event_type=TaskEvent.EventType.CREATED, events__occurred_at__gte=now - timedelta(hours=24),
        ).distinct().count(),
        'reports_to_review': Task.objects.filter(status=Task.Status.PENDING_SUPERVISOR_REVIEW).count(),
        'reports_to_approve': Task.objects.filter(status=Task.Status.COMPLETED).count(),
        'reports_late': len(_late_reports(now)),
        'new_requests': open_requests.count(),
        'escalated_requests': TicketEscalation.objects.filter(decision=TicketEscalation.Decision.PENDING).count(),
        'locked_staff': Technician.objects.annotate(login=Lower('user__username')).filter(
            login__in=locked, is_active=True).count() if locked else 0,
        'locked_customers': PortalUser.objects.annotate(login=Lower('user__username')).filter(
            login__in=locked, customer__is_active=True).count() if locked else 0,
    }
