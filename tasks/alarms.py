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


def overdue_unscheduled_tasks(country, technician, now=None):
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
        ) > UNSCHEDULED_ALARM_HOURS
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
    Support Manager). Nobody of those, active with a login: the admins, so
    a ticket is never left unseen."""
    from people.models import NotificationSettings, RolePermission

    roles = NotificationSettings.load().new_ticket_roles
    everywhere = RolePermission.objects.filter(
        permission=RolePermission.Permission.TICKETS_ALL_COUNTRIES, allowed=True,
    ).values('role')
    people = Technician.objects.filter(
        Q(country=country) | Q(role__in=everywhere), is_active=True, role__in=roles, user__isnull=False,
    )
    if not people.exists():
        people = Technician.objects.filter(role=Technician.Role.ADMIN, is_active=True, user__isnull=False)
    return people
