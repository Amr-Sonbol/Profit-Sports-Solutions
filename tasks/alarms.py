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
    hours after they were created. A supervisor sees the ones they're
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
        if working_hours_between(task.created_at, now, tz, weekend) > UNSCHEDULED_ALARM_HOURS
    ]
