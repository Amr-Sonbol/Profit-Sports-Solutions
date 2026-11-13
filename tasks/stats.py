"""The figures: certification and its points, reliability, the dashboard and its
charts, a person's progress, Team progress and Team today, hours. Shared by
the website's pages and the app's API; none of it changes anything.
"""
from collections import defaultdict
from datetime import timedelta
from zoneinfo import ZoneInfo
from django.db.models import (
    BooleanField, Count, Exists, ExpressionWrapper, Max, Min, OuterRef, Prefetch,
    Q, Subquery, Sum,
)
from django.db.models.functions import TruncMonth, TruncWeek
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.translation import gettext as _
from customers.models import Asset
from people.models import (
    RELIABLE_LEVEL, RolePermission, Technician, TechnicianConduct,
    TechnicianSkill, TechnicianTrip,
)
from people.permissions import (
    get_active_country, tasks_for_role,
)
from reference.models import ConductArea, Skill
from reports.models import WorkReport
from .models import (
    CustomerTicket, ScheduleChangeRequest, Task, TaskAssignment, TaskEvent, TicketEscalation,
)
from .equipment import (  # noqa: E402,F401
    BeyondStock, ChecklistFormSet, DISPATCH_PHOTO_MISSING, EQUIPMENT_STEPS, FOLLOW_UP_STATUSES, NEXT_STEPS, SITE_NOT_READY, STILL_TO_INSTALL, _save_checklist, check_beyond_stock, checklist_formset_rows, checklist_initial, create_follow_up, create_next_step, dispatch_photo_missing, equipment_step, has_checklist, is_installation, lines_to_carry, loading_shortfall, make_checklist_formset, next_step_sites, next_step_supervisor, next_step_supervisors, open_equipment_follow_up, register_installed_machines, save_checklist, save_planned_lines, save_site_readiness, site_is_ready, site_readiness_for_app, still_to_install, warehouse_stock_for_planning,
)
from .notices import (  # noqa: E402,F401
    CARRIER_TRACKING_URLS, CUSTOMER_EMAIL_TEMPLATES, CUSTOMER_SIGNATURE_TEXT, MAX_EMAIL_ATTACHMENT_BYTES, REMINDER_FROM_HOUR, REMINDER_NOTICE, _in_background, _send_delay_notice, _send_feedback_email, _send_notification_email, _send_schedule_notification, _send_shipment_notice_to_supervisor, _send_shipping_notice, send_visit_reminders, send_visit_report_copy, tell_customer_on_the_way,
)
from .notices import (  # noqa: E402,F401
    STAFF_LINK_SALT, STAFF_LINK_VALID_DAYS, _send_escalation_decided_email, _send_escalation_email, _send_new_task_email_to_staff, _send_reply_to_customer, _send_reply_to_staff, _send_request_closed, _staff_link_fingerprint, _staff_notification_recipients, make_staff_link_token, send_new_ticket_email_to_staff, send_staff_setup_link, send_ticket_confirmation_to_customer, staff_user_for_link_token,
)


def _certification_reference():
    """The active skills (non-cardio, cardio) and conduct areas the bar counts —
    read once for a whole roster rather than once per person."""
    return (
        list(Skill.objects.filter(is_active=True, category=Skill.Category.OTHER).values_list('id', flat=True)),
        list(Skill.objects.filter(is_active=True, category=Skill.Category.CARDIO).values_list('id', flat=True)),
        list(ConductArea.objects.filter(is_active=True).values_list('id', flat=True)),
    )


def _confirmed_levels(technicians):
    """{technician id: ({skill id: level}, {conduct area id: level})}, supervisor-confirmed only,
    for many technicians in two queries."""
    levels = {technician.pk: ({}, {}) for technician in technicians}
    for technician_id, skill_id, level in TechnicianSkill.objects.filter(
        technician__in=list(levels), source=TechnicianSkill.Source.SUPERVISOR,
    ).values_list('technician_id', 'skill_id', 'level'):
        levels[technician_id][0][skill_id] = level
    for technician_id, area_id, level in TechnicianConduct.objects.filter(
        technician__in=list(levels), source=TechnicianConduct.Source.SUPERVISOR,
    ).values_list('technician_id', 'conduct_area_id', 'level'):
        levels[technician_id][1][area_id] = level
    return levels


def _certification_status(technician, reference=None, levels=None):
    """Confirmed-only progress toward the "reliable technician" bar: level
    >= RELIABLE_LEVEL on every non-cardio skill and every conduct area,
    counting only supervisor-confirmed ratings — a self-rating never counts
    on its own (docs/database_design_v2.md, §3). Cardio (Advanced-level)
    skills are excluded from the bar; clearing one just marks readiness in
    that additional skill set, tracked separately as `cardio_ready`.
    """
    # A roster passes what it read once for everyone (_certification_reference, _confirmed_levels).
    confirmed_skill_levels, confirmed_conduct_levels = levels or _confirmed_levels([technician])[technician.pk]
    non_cardio_ids, cardio_ids, conduct_ids = reference or _certification_reference()

    non_cardio_certified = sum(1 for i in non_cardio_ids if confirmed_skill_levels.get(i, 0) >= RELIABLE_LEVEL)
    conduct_certified = sum(1 for i in conduct_ids if confirmed_conduct_levels.get(i, 0) >= RELIABLE_LEVEL)
    total_required = len(non_cardio_ids) + len(conduct_ids)
    total_certified = non_cardio_certified + conduct_certified

    return {
        'non_cardio_certified': non_cardio_certified,
        'non_cardio_total': len(non_cardio_ids),
        'conduct_certified': conduct_certified,
        'conduct_total': len(conduct_ids),
        'is_certified': total_required > 0 and total_certified == total_required,
        'cardio_ready': any(confirmed_skill_levels.get(i, 0) >= RELIABLE_LEVEL for i in cardio_ids),
    }


def _ninety_day_progress(technician, certification):
    """Where a technician stands against the 90-day certification target —
    facts (day count, confirmed vs required), not a verdict. `on_track`
    is one simple, disclosed comparison (confirmed share vs. elapsed
    share of the 90 days) — a supervisor still judges what to do about
    it; this never blocks or changes an assignment on its own.
    """
    if technician.hired_on is None:
        return None

    day_count = (timezone.localtime().date() - technician.hired_on).days
    total_required = certification['non_cardio_total'] + certification['conduct_total']
    total_certified = certification['non_cardio_certified'] + certification['conduct_certified']

    expected_fraction = min(day_count / NINETY_DAY_TRACK_DAYS, 1)
    actual_fraction = (total_certified / total_required) if total_required else 1

    return {
        'day_count': max(day_count, 0),
        'days_remaining': max(NINETY_DAY_TRACK_DAYS - day_count, 0),
        'total_certified': total_certified,
        'total_required': total_required,
        'on_track': actual_fraction >= expected_fraction,
    }


def _confirmed_points(technician):
    """Sum of supervisor-confirmed levels across every non-cardio skill and
    conduct area — the ranking number for the leaderboard. Self-ratings and
    cardio skills don't count, matching the certification bar above.
    """
    skill_points = TechnicianSkill.objects.filter(
        technician=technician, source=TechnicianSkill.Source.SUPERVISOR, skill__category=Skill.Category.OTHER,
    ).aggregate(total=Sum('level'))['total'] or 0
    conduct_points = TechnicianConduct.objects.filter(
        technician=technician, source=TechnicianConduct.Source.SUPERVISOR,
    ).aggregate(total=Sum('level'))['total'] or 0
    return skill_points + conduct_points


def _leaderboard(country):
    """Technicians in this country ranked by confirmed certification
    points, highest first. Visibility only — never used to change a level."""
    technicians = list(Technician.objects.filter(
        is_active=True, country=country, role=Technician.Role.TECHNICIAN,
    ).order_by('full_name'))
    # Everyone's points in two queries (the same sums as _confirmed_points, not two per person).
    points = defaultdict(int)
    for technician_id, total in TechnicianSkill.objects.filter(
        technician__in=technicians, source=TechnicianSkill.Source.SUPERVISOR, skill__category=Skill.Category.OTHER,
    ).values('technician_id').annotate(total=Sum('level')).values_list('technician_id', 'total'):
        points[technician_id] += total or 0
    for technician_id, total in TechnicianConduct.objects.filter(
        technician__in=technicians, source=TechnicianConduct.Source.SUPERVISOR,
    ).values('technician_id').annotate(total=Sum('level')).values_list('technician_id', 'total'):
        points[technician_id] += total or 0
    return sorted(((t, points[t.pk]) for t in technicians), key=lambda pair: pair[1], reverse=True)


def _median_minutes(deltas):
    if not deltas:
        return None
    ordered = sorted(deltas)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[mid])
    return round((ordered[mid - 1] + ordered[mid]) / 2)


def _reliability_stats(technician):
    """Facts only, never a combined score — docs/database_design_v2.md
    §6's "months 4-8" stage: plain tasks-completed and on-time-arrival
    numbers, nothing inferred yet needing more volume (first-time fix
    rate stays out entirely; it needs the asset register maturing over
    real time, not just a query). Only the lead's record is affected by
    an outcome — helpers get a participation count, nothing else.
    Broken down by brand, since one combined number hides exactly what
    matters; a brand's own on-time % is hidden below
    RELIABILITY_MIN_SAMPLE completed tasks for that brand specifically,
    same reasoning as the overall figures below.
    """
    timing_events = TaskEvent.objects.filter(
        event_type__in=[
            TaskEvent.EventType.ARRIVED, TaskEvent.EventType.ASSIGNED, TaskEvent.EventType.ACCEPTED,
        ],
    ).order_by('occurred_at')

    led_tasks = list(
        Task.objects.filter(
            assignments__technician=technician, assignments__role=TaskAssignment.Role.LEAD,
            assignments__is_active=True, status__in=[Task.Status.COMPLETED, Task.Status.CLOSED],
        )
        .select_related('brand')
        .prefetch_related(Prefetch('events', queryset=timing_events, to_attr='timing_events'))
        .distinct(),
    )
    helped_count = Task.objects.filter(
        assignments__technician=technician, assignments__role=TaskAssignment.Role.HELPER,
        assignments__is_active=True, status__in=[Task.Status.COMPLETED, Task.Status.CLOSED],
    ).distinct().count()

    def _on_time_stats(tasks):
        counted = 0
        on_time = 0
        for task in tasks:
            if task.status == Task.Status.BLOCKED or not task.promised_at:
                continue
            arrived = next(
                (e for e in task.timing_events if e.event_type == TaskEvent.EventType.ARRIVED), None,
            )
            if not arrived:
                continue
            counted += 1
            if arrived.effective_at <= task.promised_at:
                on_time += 1
        if counted < RELIABILITY_MIN_SAMPLE:
            return None, counted
        return round(on_time / counted * 100), counted

    def _acceptance_latency(tasks):
        deltas = []
        for task in tasks:
            assigned = next(
                (e for e in task.timing_events if e.event_type == TaskEvent.EventType.ASSIGNED), None,
            )
            accepted = next(
                (e for e in task.timing_events if e.event_type == TaskEvent.EventType.ACCEPTED), None,
            )
            if assigned and accepted and accepted.effective_at > assigned.effective_at:
                deltas.append((accepted.effective_at - assigned.effective_at).total_seconds() / 60)
        return _median_minutes(deltas) if len(deltas) >= RELIABILITY_MIN_SAMPLE else None

    overall_on_time_percent, overall_sample = _on_time_stats(led_tasks)

    by_brand = defaultdict(list)
    for task in led_tasks:
        by_brand[task.brand].append(task)

    brand_breakdown = []
    for brand, tasks in sorted(by_brand.items(), key=lambda pair: pair[0].name if pair[0] else '￿'):
        on_time_percent, sample = _on_time_stats(tasks)
        brand_breakdown.append({
            'brand': brand,
            'completed': len(tasks),
            'on_time_percent': on_time_percent,
            'sample_size': sample,
        })

    return {
        'total_completed': len(led_tasks),
        'total_helped': helped_count,
        'on_time_percent': overall_on_time_percent,
        'on_time_sample_size': overall_sample,
        'acceptance_latency_minutes': _acceptance_latency(led_tasks),
        'by_brand': brand_breakdown,
        'min_sample': RELIABILITY_MIN_SAMPLE,
    }


# Open tasks shown on the dashboard before "See all".
DASHBOARD_TASK_LIMIT = 10
# The most any dashboard shows (the app's: 15) — all dashboard_overview reads.
DASHBOARD_OVERVIEW_TASKS = 15
# Periods in the dashboard's charts, the current one included: monthly by
# default (what managers plan by), weekly on request.
DASHBOARD_CHART_MONTHS = 12
DASHBOARD_CHART_WEEKS = 8


def _nice_ceiling(value):
    """The smallest of 4, 6, 10, 20, 40, 60, 100, ... at or above `value`: a
    chart's top gridline, always even so the middle one is a whole number.
    """
    step = 1
    while True:
        for factor in (2, 4, 6, 10):
            if factor * step >= max(value, 4):
                return factor * step
        step *= 10


def _period_starts(period):
    """The first day of each chart period, oldest first: the last
    DASHBOARD_CHART_MONTHS months, or the last DASHBOARD_CHART_WEEKS
    Monday-based weeks, the current one included.
    """
    today = timezone.localdate()
    if period == 'month':
        starts = [today.replace(day=1)]
        while len(starts) < DASHBOARD_CHART_MONTHS:
            starts.insert(0, (starts[0] - timedelta(days=1)).replace(day=1))
        return starts
    this_monday = today - timedelta(days=today.weekday())
    return [this_monday - timedelta(weeks=n) for n in range(DASHBOARD_CHART_WEEKS - 1, -1, -1)]


def _period_chart(opened, completed, period, starts=None):
    """Counts per period (see _period_starts, or the given `starts`) from
    two lists of datetimes (or counts already made per period —
    _counted_by_period): what came in and what was finished. Bar
    heights are percentages of a clean top value, so the template draws
    them without arithmetic. Shared by the dashboard's tickets and tasks
    charts and My progress.
    """
    starts = starts or _period_starts(period)
    buckets = {start: {'opened': 0, 'completed': 0} for start in starts}

    def bucket_of(moment):
        day = timezone.localtime(moment).date()
        return day.replace(day=1) if period == 'month' else day - timedelta(days=day.weekday())

    for key, moments in (('opened', opened), ('completed', completed)):
        if isinstance(moments, dict):  # counted by the database already
            for start, count in moments.items():
                if start in buckets:
                    buckets[start][key] += count
            continue
        for moment in moments:
            bucket = buckets.get(bucket_of(moment))
            if bucket is not None:
                bucket[key] += 1

    top = _nice_ceiling(max(max(b['opened'], b['completed']) for b in buckets.values()))
    return {
        'period': period,
        'top': top,
        'ticks': [top, top // 2, 0],
        'periods': [
            {
                'start': start, 'is_current': start == starts[-1], **counts,
                'opened_pct': round(counts['opened'] * 100 / top), 'completed_pct': round(counts['completed'] * 100 / top),
            }
            for start, counts in buckets.items()
        ],
    }


def _counted_by_period(queryset, field, period):
    """{first day of each month (or week): how many} of `queryset` by the moment in
    `field` — counted by the database (a dozen numbers), not row by row here."""
    trunc = TruncMonth if period == 'month' else TruncWeek  # weeks from Monday, as _period_starts
    return {
        row['start'].date() if hasattr(row['start'], 'date') else row['start']: row['n']
        for row in queryset.order_by().annotate(start=trunc(field)).values('start').annotate(n=Count('pk'))
    }


def _tasks_chart(country, period):
    """Tasks reported vs tasks whose first work report was submitted, per period."""
    since = _period_starts(period)[0]
    tasks = Task.objects.filter(site__customer__country=country)
    opened = _counted_by_period(tasks.filter(reported_at__date__gte=since), 'reported_at', period)
    # Each task's first report, then counted per period by the database too.
    first_report = TaskEvent.objects.filter(
        task=OuterRef('pk'), event_type=TaskEvent.EventType.REPORT_SUBMITTED,
    ).order_by('occurred_at').values('occurred_at')[:1]
    completed = _counted_by_period(
        tasks.annotate(first=Subquery(first_report)).filter(first__date__gte=since), 'first', period,
    )
    return _period_chart(opened, completed, period)


def _my_trend_chart(technician, firsts=None):
    """One person's tasks completed per month, as a line (My progress):
    the tasks they were on (lead or helper) by their first work report.
    It starts the month their account was created, not before (so a new
    technician doesn't start on months of nothing), and shows at most the
    last year. Each month's `completed_pct` places its point; `line_points`
    is the SVG polyline through them (x at each month's centre, 0-100 both
    ways); `change` is this month against last, None in their first month.
    """
    joined = timezone.localtime(technician.user.date_joined).date().replace(day=1)
    starts = [start for start in _period_starts('month') if start >= joined] or _period_starts('month')[-1:]
    since = starts[0]
    if firsts is None:
        firsts = _first_reports([technician], since)[technician.pk]
    # (`firsts`: a roster passes what it read once for everyone — _first_reports.)
    completed = [first for first in firsts if timezone.localtime(first).date() >= since]
    chart = _period_chart([], completed, 'month', starts=starts)
    periods = chart['periods']
    chart['line_points'] = ' '.join(
        f"{(index + 0.5) * 100 / len(periods):.2f},{100 - period['completed_pct']}"
        for index, period in enumerate(periods)
    )
    chart['this_month'] = periods[-1]['completed']
    chart['change'] = periods[-1]['completed'] - periods[-2]['completed'] if len(periods) > 1 else None
    chart['change_size'] = abs(chart['change'] or 0)
    return chart


def _tickets_chart(country, period):
    """Customer tickets received vs resolved (converted, dismissed or closed: reviewed_at), per period."""
    since = _period_starts(period)[0]
    tickets = CustomerTicket.objects.filter(kind=CustomerTicket.Kind.CUSTOMER, country=country)
    return _period_chart(
        _counted_by_period(tickets.filter(submitted_at__date__gte=since), 'submitted_at', period),
        _counted_by_period(tickets.filter(reviewed_at__date__gte=since), 'reviewed_at', period),
        period,
    )


# Escalations listed one by one on the home page.
HOME_ESCALATION_LIMIT = 5

# A single Exists, not exclude(assignments__role=..., assignments__is_active=...):
# exclude() lets those two match different assignments.
HAS_ACTIVE_LEAD = Exists(
    TaskAssignment.objects.filter(task=OuterRef('pk'), role=TaskAssignment.Role.LEAD, is_active=True),
)


def home_summary(request, technician):
    """The home page: what's waiting on this person, each count linking to
    the list behind it, and their own visits today. Each count is gated
    by the same permission as the page it links to.
    """
    country = get_active_country(request)
    allowed = set(
        RolePermission.objects.filter(role=technician.permission_role, allowed=True).values_list('permission', flat=True),
    )
    task_list_url = reverse('tasks:task_list')
    open_tasks = tasks_for_role(technician, Task.objects.filter(status__in=OPEN_STATUSES, site__customer__country=country))
    actions = []

    def add(label, count, url, level='warning'):
        if count:
            actions.append({'label': label, 'count': count, 'url': url, 'level': level})

    if RolePermission.Permission.VIEW_TASKS in allowed:
        add(_('Emergencies'), open_tasks.filter(priority=Task.Priority.EMERGENCY).count(),
            f'{task_list_url}?priority=emergency', 'critical')
    if RolePermission.Permission.ASSIGN_TASKS in allowed:
        add(_('Tasks without a lead technician'), open_tasks.exclude(HAS_ACTIVE_LEAD).count(), f'{task_list_url}?lead=none')
        add(_('Tasks not scheduled'), open_tasks.filter(scheduled_for__isnull=True, scheduled_date__isnull=True).count(),
            f'{task_list_url}?scheduled=none')
    if RolePermission.Permission.VIEW_TASKS in allowed:
        if technician.role == Technician.Role.SUPPORT_MANAGER:
            add(_('Reports awaiting your approval'), open_tasks.filter(status=Task.Status.COMPLETED).count(),
                f'{task_list_url}?status=completed')
        else:
            add(_('Reports awaiting your review'),
                open_tasks.filter(status=Task.Status.PENDING_SUPERVISOR_REVIEW, responsible_supervisor=technician).count(),
                f'{task_list_url}?status=pending_supervisor_review&supervisor={technician.pk}')
    if RolePermission.Permission.MANAGE_TICKETS in allowed:
        add(_('New customer tickets'), CustomerTicket.objects.filter(
            kind=CustomerTicket.Kind.CUSTOMER, status=CustomerTicket.Status.NEW, country=country,
        ).count(), reverse('tasks:ticket_list'))
    if {RolePermission.Permission.MANAGE_CUSTOMERS, RolePermission.Permission.VIEW_MACHINES} <= allowed:
        add(_('Machines added by customers, to confirm'), Asset.objects.filter(
            status=Asset.Status.PENDING, site__customer__country=country,
        ).count(), f"{reverse('tasks:machine_list')}?status=pending")
    if technician.role != Technician.Role.TECHNICIAN:
        add(_('Internal tasks assigned to you'), CustomerTicket.objects.filter(
            assigned_to=technician, status=CustomerTicket.Status.NEW, country=country,
        ).exclude(kind=CustomerTicket.Kind.CUSTOMER).count(), reverse('tasks:internal_tickets'))
    escalations = TicketEscalation.objects.filter(
        escalated_to=technician, decision=TicketEscalation.Decision.PENDING,
    ).select_related('ticket').order_by('escalated_at')[:HOME_ESCALATION_LIMIT]
    for escalation in escalations:
        actions.append({
            'label': _('Escalation awaiting your decision: %(company)s') % {'company': escalation.ticket.company_name},
            'count': None, 'url': reverse('tasks:ticket_review', args=[escalation.ticket_id]), 'level': 'critical',
        })

    today = timezone.localdate()
    visits = list(
        Task.objects.filter(
            assignments__technician=technician, assignments__is_active=True, status__in=OPEN_STATUSES,
        ).filter(Q(scheduled_for__date=today) | Q(scheduled_date=today) | Q(status=Task.Status.IN_PROGRESS))
        .select_related('site__customer').distinct().order_by('scheduled_for', 'task_number'),
    )
    return {
        'actions': actions,
        # A field technician has nothing to approve or assign; an empty
        # "needs your action" box would only push their visits down.
        'show_actions': bool(actions) or technician.role != Technician.Role.TECHNICIAN,
        'visits': visits,
        # The manager tier doesn't go on visits, so no empty "today" box for them.
        'show_visits': bool(visits) or not technician.is_manager_tier,
        'today': today,
    }


def waiting_for(technician, country):
    """What's waiting on this person's decision, one item each, oldest first —
    the app's Tasks → Waiting for me and the top of its Dashboard. The same
    rules as the actions themselves: a report to review (its supervisor), a
    report to approve (a manager), a schedule change to approve (a manager
    who owns the task), a blocked task they own, an escalated request sent to
    them, an internal task given to them.
    """
    allowed = set(RolePermission.objects.filter(role=technician.permission_role, allowed=True).values_list('permission', flat=True))
    items = []

    def task_item(kind, title, task, at, detail=''):
        items.append({
            'kind': kind, 'title': title, 'at': at, 'detail': detail,
            'text': f'{task.task_number} · {task.site.customer.name} — {task.site.name}',
            'opens': {'screen': 'team-task', 'id': task.pk},
        })

    if RolePermission.Permission.VIEW_TASKS in allowed:
        open_tasks = tasks_for_role(technician, Task.objects.filter(
            status__in=OPEN_STATUSES, site__customer__country=country,
        )).select_related('site__customer')
        if technician.role == Technician.Role.SUPPORT_MANAGER:  # the final approval is theirs
            for task in open_tasks.filter(status=Task.Status.COMPLETED).annotate(
                    at=Max('events__occurred_at')).order_by('at')[:50]:
                task_item('approve_report', _('Report to approve'), task, task.at)
        for task in open_tasks.filter(
                status=Task.Status.PENDING_SUPERVISOR_REVIEW, responsible_supervisor=technician,
        ).annotate(at=Max('events__occurred_at')).order_by('at')[:50]:
            task_item('review_report', _('Report to review'), task, task.at)
        if technician.is_manager_tier:
            for change in ScheduleChangeRequest.objects.filter(
                    status=ScheduleChangeRequest.Status.PENDING, task__in=open_tasks,
            ).select_related('task__site__customer', 'requested_by').order_by('created_at')[:50]:
                if task_owned_by(technician, change.task):
                    when = formats.date_format(timezone.localtime(change.requested_scheduled_for), 'D j M, H:i')
                    task_item('schedule_change', _('Visit time change to approve'), change.task, change.created_at,
                              _('Asked for %(when)s') % {'when': when} + (f' — {change.reason}' if change.reason else ''))
        for task in open_tasks.filter(status=Task.Status.BLOCKED, responsible_supervisor=technician).annotate(
                at=Max('events__occurred_at', filter=Q(events__event_type=TaskEvent.EventType.BLOCKED))).order_by('at')[:50]:
            task_item('blocked', _('Blocked'), task, task.at)
    from tasks.alarms import late_reports
    for task in late_reports(technician)[:50]:  # the Operations Manager's safety net
        task_item('late_report', _('Report waiting %(days)s days for approval') % {'days': task.waiting_days},
                  task, task.waiting_since, _('Waiting for the Technical Support Manager — you may approve it instead.'))
    for escalation in TicketEscalation.objects.filter(
            escalated_to=technician, decision=TicketEscalation.Decision.PENDING,
    ).select_related('ticket').order_by('escalated_at')[:50]:
        items.append({
            'kind': 'escalation', 'title': _('Escalated to you'), 'at': escalation.escalated_at,
            'text': f'{escalation.ticket.ticket_number} · {escalation.ticket.company_name}', 'detail': escalation.reason,
            'opens': {'screen': 'ticket', 'id': escalation.ticket_id},
        })
    if technician.role != Technician.Role.TECHNICIAN:
        for ticket in CustomerTicket.objects.filter(
                assigned_to=technician, status=CustomerTicket.Status.NEW, country=country,
        ).exclude(kind=CustomerTicket.Kind.CUSTOMER).order_by('submitted_at')[:50]:
            items.append({
                'kind': 'internal', 'title': _('Internal task for you'), 'at': ticket.submitted_at,
                'text': f'{ticket.ticket_number} · {ticket.subject or ticket.get_kind_display()}', 'detail': '',
                'opens': {'screen': 'internal-task', 'id': ticket.pk},
            })
    items.sort(key=lambda item: item['at'] or timezone.now())
    return items


def technician_workload(technicians, country):
    """Each technician's work still to do, by its day in `country`: today,
    upcoming (later, or no date yet) and overdue (its day has passed). Work
    done and waiting for review or closing doesn't count. {technician id:
    {'today', 'upcoming', 'overdue'}}."""
    to_do = [Task.Status.NEW, Task.Status.ASSIGNED, Task.Status.ACCEPTED, Task.Status.IN_PROGRESS, Task.Status.BLOCKED]
    country_zone = ZoneInfo(country.timezone)
    today = timezone.now().astimezone(country_zone).date()
    workload = {technician.id: {'today': 0, 'upcoming': 0, 'overdue': 0} for technician in technicians}
    for technician_id, scheduled_for, scheduled_date in TaskAssignment.objects.filter(
        technician__in=technicians, is_active=True, task__status__in=to_do,
    ).values_list('technician_id', 'task__scheduled_for', 'task__scheduled_date'):
        day = scheduled_for.astimezone(country_zone).date() if scheduled_for else scheduled_date
        when = 'upcoming' if day is None or day > today else 'today' if day == today else 'overdue'
        workload[technician_id][when] += 1
    return workload


def dashboard_overview(active_country, viewer=None):
    """The dashboard's figures for a country: its technicians (with their
    work still to do: today, upcoming, overdue), every open task in "needs someone first" order
    (emergencies, then no lead, then no date) and the counts. The website's
    Dashboard and the app's (api.views.DashboardView).
    """
    technicians = list(Technician.objects.filter(
        is_active=True, country=active_country, role=Technician.Role.TECHNICIAN,
    ).order_by('full_name'))
    workload = technician_workload(technicians, active_country)
    for technician in technicians:
        technician.workload = workload[technician.id]

    open_tasks = Task.objects.filter(status__in=OPEN_STATUSES, site__customer__country=active_country)
    if viewer is not None:  # as this role sees tasks (a Warehouse Manager: stock work only)
        open_tasks = tasks_for_role(viewer, open_tasks)
    # Counted and put in order by the database: only the tasks shown are read (not every open one).
    open_tasks = open_tasks.annotate(
        has_lead=Exists(TaskAssignment.objects.filter(
            task=OuterRef('pk'), role=TaskAssignment.Role.LEAD, is_active=True,
        )),
        unscheduled=ExpressionWrapper(
            Q(scheduled_for__isnull=True, scheduled_date__isnull=True), output_field=BooleanField(),
        ),
        is_emergency=ExpressionWrapper(Q(priority=Task.Priority.EMERGENCY), output_field=BooleanField()),
        priority_rank=PRIORITY_RANK,
    )
    stats = {
        'available_count': sum(1 for t in technicians if t.is_available),
        'technician_count': len(technicians),
        'open_task_count': open_tasks.count(),
        'unassigned_count': open_tasks.filter(has_lead=False).count(),
        'unscheduled_count': open_tasks.filter(unscheduled=True).count(),
        'emergency_count': open_tasks.filter(priority=Task.Priority.EMERGENCY).count(),
    }
    # What needs someone first: emergencies, then no lead, then no date; within each,
    # by priority, then the scheduled time, then what the customer was promised.
    tasks = list(_with_lead_prefetch(
        open_tasks.select_related('site__customer', 'task_type').order_by(
            '-is_emergency', 'has_lead', '-unscheduled', 'priority_rank', 'scheduled_for', 'promised_at',
        )[:DASHBOARD_OVERVIEW_TASKS],
    ))
    _attach_lead_technician(tasks)
    for task in tasks:
        task.is_unscheduled = task.unscheduled

    return technicians, tasks, stats


def progress_context(request, technician):
    """Everything My progress shows, for `technician`."""
    skills = _skills_with_current_rating(technician)
    conduct_areas = _conduct_areas_with_current_rating(technician)
    certification = _certification_status(technician)
    ninety_day = _ninety_day_progress(technician, certification)
    reliability = _reliability_stats(technician)

    leaderboard = _leaderboard(technician.country)
    # Ranked once they have points: a newcomer isn't "last of 29".
    rank = next(
        (position for position, (t, points) in enumerate(leaderboard, start=1) if t.pk == technician.pk and points), None,
    )

    _today, start, end = _week_window(request, technician.country)
    my_assignments = TaskAssignment.objects.filter(technician=technician, is_active=True)

    assigned_count = Task.objects.filter(
        Q(pk__in=my_assignments.values('task_id')),
        Q(scheduled_for__date__range=(start, end))
        | (Q(scheduled_for__isnull=True) & Q(status__in=OPEN_STATUSES)),
    ).count()

    # Their own taps — none for someone without a login.
    completed_count = TaskEvent.objects.filter(
        actor_id=technician.user_id, event_type=TaskEvent.EventType.COMPLETED, occurred_at__date__range=(start, end),
    ).count() if technician.user_id else 0

    # Same rule and figure as the supervisors' Hours page — lead, helper
    # and handover hours alike. Any past month (?month=YYYY-MM), this one
    # by default; never a future one, which has nothing yet.
    this_month = timezone.localtime().date().replace(day=1)
    month_first = min(_resolve_report_month(request), this_month)
    month_start, month_end = _month_window(month_first)
    month_reports = list(
        WorkReport.objects.filter(
            submitted_at__gte=month_start, submitted_at__lt=month_end,
            task__assignments__technician=technician, task__assignments__role=TaskAssignment.Role.LEAD,
            task__assignments__is_active=True,
        ).select_related('task').distinct(),
    )
    own_row = next(
        (row for row in _technician_hours_rows(technician.country, month_first) if row['technician'] == technician),
        None,
    )

    context = {
        'trend_chart': _my_trend_chart(technician),
        'month_first': month_first,
        'prev_month': _shift_month(month_first, -1),
        'next_month': _shift_month(month_first, 1) if month_first < this_month else None,
        'is_this_month': month_first == this_month,
        'month_hours': own_row['hours'] if own_row else 0,
        'month_report_count': len(month_reports),
        'month_overrun_count': sum(1 for report in month_reports if report.is_overrun),
        'skills': skills,
        'conduct_areas': conduct_areas,
        'certification': certification,
        'ninety_day': ninety_day,
        'rank': rank,
        'leaderboard_size': len(leaderboard),
        'assigned_count': assigned_count,
        'completed_count': completed_count,
        'reliability': reliability,
    }
    return context


def _first_reports(people, since):
    """{technician id: [when each task they were on (lead or helper) was first
    reported]} — those first reported on or after `since` (a date), for a
    whole roster in two queries."""
    # .order_by(): no default sorting (by task), which would join every task for nothing.
    theirs = TaskAssignment.objects.filter(technician__in=people).order_by()
    on = defaultdict(list)
    for technician_id, task_id in theirs.values_list('technician_id', 'task_id').distinct():
        on[task_id].append(technician_id)
    firsts = {person.pk: [] for person in people}
    # The tasks are found by the database itself (a subquery), not sent to it as a list of thousands.
    for task_id, first in TaskEvent.objects.filter(
        event_type=TaskEvent.EventType.REPORT_SUBMITTED, task_id__in=theirs.values('task_id'),
    ).order_by().values('task_id').annotate(first=Min('occurred_at')).filter(
        first__date__gte=since,
    ).values_list('task_id', 'first'):
        for technician_id in on[task_id]:
            firsts[technician_id].append(first)
    return firsts


def _completed_in_month(technician, month_first):
    """How many tasks someone was on (lead or helper) had their first work
    report in that month — the figure My progress's line counts."""
    start, end = _month_window(month_first)
    mine = TaskAssignment.objects.filter(technician=technician).values('task_id')
    return TaskEvent.objects.filter(
        event_type=TaskEvent.EventType.REPORT_SUBMITTED, task_id__in=mine,
    ).values('task_id').annotate(first=Min('occurred_at')).filter(first__gte=start, first__lt=end).count()


# What a person's last tap today says about where they are now (team_today).
_NOW_FROM_TAP = {
    TaskEvent.EventType.EN_ROUTE: 'on_the_way', TaskEvent.EventType.ARRIVED: 'on_site',
    TaskEvent.EventType.STARTED: 'working', TaskEvent.EventType.RESUMED: 'working',
    TaskEvent.EventType.PAUSED: 'paused', TaskEvent.EventType.BLOCKED: 'blocked',
}


def team_today(viewer, country):
    """The field team `viewer` oversees, as it is right now — the app's Team →
    Today: off (sick, leave, holiday) or abroad on a trip; otherwise what their
    last tap today says (on the way, on site, working, paused, blocked) or free,
    with the task it's about, their next job today, how many of today's jobs are
    done, where their last tap was, and their phone. People visiting from another
    country on a trip are in it too. Same people as Team progress (team_progress_rows).
    """
    tz = ZoneInfo(country.timezone)
    today = timezone.now().astimezone(tz).date()
    visiting = TechnicianTrip.objects.filter(country=country, start_date__lte=today, end_date__gte=today)
    abroad = {
        trip.technician_id: trip for trip in TechnicianTrip.objects.filter(
            start_date__lte=today, end_date__gte=today,
        ).exclude(country=country).select_related('country')
    }
    people = list(_people_i_oversee(viewer).filter(
        Q(country=country) | Q(pk__in=visiting.values('technician_id')),
        is_active=True, role__in=[Technician.Role.SUPERVISOR, Technician.Role.TECHNICIAN],
    ).select_related('country', 'user').order_by('full_name'))
    user_ids = [person.user_id for person in people if person.user_id]
    last_taps = {}
    for tap in TaskEvent.objects.filter(
        actor_id__in=user_ids, occurred_at__date=today, event_type__in=list(_NOW_FROM_TAP) + [
            TaskEvent.EventType.COMPLETED,
        ],
    ).select_related('task__site__customer').order_by('-occurred_at'):
        last_taps.setdefault(tap.actor_id, tap)
    todays = TaskAssignment.objects.filter(
        technician__in=people, is_active=True, task__scheduled_for__date=today,
    ).exclude(task__status=Task.Status.CANCELLED).select_related('task__site__customer').order_by('task__scheduled_for')
    by_person = {}
    for assignment in todays:
        by_person.setdefault(assignment.technician_id, []).append(assignment.task)

    def task_ref(task):
        return {'id': task.pk, 'task_number': task.task_number, 'customer': task.site.customer.name,
                'site': task.site.name, 'scheduled_for': task.scheduled_for}

    rows = []
    for person in people:
        tasks = by_person.get(person.pk, [])
        done = [task for task in tasks if task.status in (
            Task.Status.PENDING_SUPERVISOR_REVIEW, Task.Status.COMPLETED, Task.Status.CLOSED,
        )]
        tap = last_taps.get(person.user_id)
        state, about = 'free', None
        if not person.is_available:
            state = 'off'
        elif person.pk in abroad and person.country_id == country.pk:
            state = 'abroad'
        elif tap is not None and tap.event_type in _NOW_FROM_TAP and tap.task.status not in (
            Task.Status.PENDING_SUPERVISOR_REVIEW, Task.Status.COMPLETED, Task.Status.CLOSED, Task.Status.CANCELLED,
        ):
            state, about = _NOW_FROM_TAP[tap.event_type], tap.task
        upcoming = [task for task in tasks if task not in done and task != about and task.scheduled_for
                    and task.scheduled_for >= timezone.now() - timedelta(hours=1)]
        trip = abroad.get(person.pk)
        rows.append({
            'technician': person, 'state': state,
            'off_reason': person.get_unavailable_reason_display() if state == 'off' else '',
            'abroad': {'country': trip.country.display_name, 'until': trip.end_date} if state == 'abroad' else None,
            'visiting_from': person.country.display_name if person.country_id != country.pk else '',
            'since': tap.occurred_at if about is not None else None,
            'task': task_ref(about) if about is not None else None,
            'next': task_ref(upcoming[0]) if upcoming else None,
            'today_total': len(tasks), 'today_done': len(done),
            'location': ({'lat': float(tap.latitude), 'lng': float(tap.longitude),
                          'away': tap.location_status == TaskEvent.LocationStatus.AWAY}
                         if tap is not None and tap.latitude is not None else None),
        })
    # Who needs attention first: blocked, then at work, on the way, free, then away.
    order = ['blocked', 'working', 'on_site', 'paused', 'on_the_way', 'free', 'abroad', 'off']
    rows.sort(key=lambda row: order.index(row['state']))
    return rows


def team_progress_rows(viewer, country, month_first):
    """Team progress's rows — the field team `viewer` oversees in
    `country`, by rank. Shared with the app (api.views.TeamProgressView)."""
    previous = _shift_month(month_first, -1)
    ranks = {t.pk: position for position, (t, _points) in enumerate(_leaderboard(country), start=1)}
    hours = {row['technician'].pk: row['hours'] for row in _technician_hours_rows(country, month_first, with_taps=False)}
    rows = []
    reference = _certification_reference()
    # The field team: a manager or admin sees supervisors here too.
    team = list(_people_i_oversee(viewer).filter(
        country=country, is_active=True, role__in=[Technician.Role.SUPERVISOR, Technician.Role.TECHNICIAN],
    ).select_related('user').order_by('full_name'))
    # Read once for everyone (a query or two in all, not several per person): when
    # each task they were on was first reported — the year's line and both months.
    firsts = _first_reports(team, min(_period_starts('month')[0], previous))
    levels = _confirmed_levels(team)

    def completed_in(technician, month):
        start, end = _month_window(month)
        return sum(1 for first in firsts[technician.pk] if start <= first < end)

    for technician in team:
        completed = completed_in(technician, month_first)
        certification = _certification_status(technician, reference, levels[technician.pk])
        rows.append({
            'technician': technician,
            'rank': ranks.get(technician.pk),
            'completed': completed,
            'change': completed - completed_in(technician, previous),
            'hours': hours.get(technician.pk, 0),
            'certified': certification['non_cardio_certified'] + certification['conduct_certified'],
            'required': certification['non_cardio_total'] + certification['conduct_total'],
            'is_certified': certification['is_certified'],
            'trend': _my_trend_chart(technician, firsts[technician.pk]),
        })
    rows.sort(key=lambda row: (row['rank'] is None, row['rank'] or 0, row['technician'].full_name))
    for row in rows:
        row['change_size'] = abs(row['change'])
    return rows


def _handover_hours(technicians, period_start, period_end):
    """Hours a lead put into a job before being taken off it — the report,
    filed later by whoever took over, only credits the new lead. Keyed by
    technician id, counted in the month the handover happened.
    """
    handovers = defaultdict(list)
    for assignment in TaskAssignment.objects.filter(
        technician__in=technicians, role=TaskAssignment.Role.LEAD, is_active=False, labour_hours__isnull=False,
        ended_at__gte=period_start, ended_at__lt=period_end,
    ).select_related('task__site__customer').order_by('ended_at'):
        handovers[assignment.technician_id].append(assignment)
    return handovers


def _technician_hours_rows(active_country, period_start_date, with_taps=True):
    """One row per active technician in the country for that month: the
    work reports they filed as lead, the ones they helped on, and their
    hours — a report's hours count for everyone on the team, except where
    the lead entered a helper's own (helper_hours), plus any hours put in
    before a handover. Shared by the Hours page and its CSV export.
    `with_taps=False` (Team progress: the hours alone) skips reading each
    report's taps, which only the Hours page's flags need.
    """
    period_start, period_end = _month_window(period_start_date)
    technicians = list(Technician.objects.filter(is_active=True, country=active_country).order_by('full_name'))
    # By who worked on it, not where the task was — a technician's hours
    # on a trip abroad still count at home (docs: technician_trip).
    reports = WorkReport.objects.filter(
        submitted_at__gte=period_start, submitted_at__lt=period_end,
        task__assignments__technician__in=technicians, task__assignments__is_active=True,
    ).distinct().select_related('task__site__customer').prefetch_related(
        Prefetch(
            'task__assignments', queryset=TaskAssignment.objects.filter(is_active=True), to_attr='active_assignments',
        ),
        *(['task__events'] if with_taps else []),
    ).order_by('submitted_at')

    leads_reports = defaultdict(list)
    helped_on = defaultdict(int)
    helper_hours = defaultdict(int)
    helped_reports = defaultdict(list)
    for report in reports:
        for assignment in report.task.active_assignments:
            if assignment.role == TaskAssignment.Role.LEAD:
                leads_reports[assignment.technician_id].append(report)
            else:
                helped_on[assignment.technician_id] += 1
                hours = report.labour_hours if assignment.labour_hours is None else assignment.labour_hours
                helper_hours[assignment.technician_id] += hours
                helped_reports[assignment.technician_id].append((report, hours))

    handovers = _handover_hours(technicians, period_start, period_end)

    rows = []
    for technician in technicians:
        own_reports = leads_reports[technician.pk]
        own_handovers = handovers[technician.pk]
        rows.append({
            'technician': technician,
            'reports': own_reports,
            'handovers': own_handovers,
            'helped_reports': helped_reports[technician.pk],
            'hours': (
                sum(report.labour_hours for report in own_reports)
                + helper_hours[technician.pk]
                + sum(assignment.labour_hours for assignment in own_handovers)
            ),
            'estimated': sum(report.task.estimated_hours for report in own_reports if report.task.estimated_hours),
            'helped_on': helped_on[technician.pk],
            # The Hours page's flags, from each report's taps (not read for the hours alone).
            'overrun_count': sum(1 for report in own_reports if report.is_overrun) if with_taps else None,
            'above_tapped_count': sum(1 for report in own_reports if report.is_above_tapped) if with_taps else None,
            'away_count': sum(1 for report in own_reports if report.has_taps_away_from_site) if with_taps else None,
        })
    return rows


# Defined with the pages in tasks.views; imported last, so either module can load first.
from .views import (  # noqa: E402
    NINETY_DAY_TRACK_DAYS, OPEN_STATUSES, PRIORITY_RANK, RELIABILITY_MIN_SAMPLE, _attach_lead_technician, _conduct_areas_with_current_rating, _month_window, _people_i_oversee, _resolve_report_month, _shift_month, _skills_with_current_rating, _week_window, _with_lead_prefetch, task_owned_by,
)
