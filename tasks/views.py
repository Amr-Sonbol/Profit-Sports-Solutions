import csv
from collections import defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from email.mime.image import MIMEImage

from django.contrib import messages
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.contrib.staticfiles.finders import find as find_static_file
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, IntegerField, Prefetch, ProtectedError, Q, Sum, Value, When
from django.forms import formset_factory
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.crypto import get_random_string
from django.utils.dateparse import parse_date, parse_time
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from customers.models import Asset, Customer, Site
from people.models import (
    RELIABLE_LEVEL, NotificationSettings, RolePermission, Technician, TechnicianConduct,
    TechnicianConductAssessment, TechnicianSkill, TechnicianSkillAssessment,
)
from people.permissions import (
    ACTIVE_COUNTRY_SESSION_KEY, get_active_country, require_admin, require_manager, require_permission,
    require_technician, scoped_or_404 as _scoped_or_404,
)
from reference.models import Brand, ConductArea, Country, Skill, TaskType
from reports.forms import PartUsedItemForm, WorkReportForm
from reports.models import CustomerFeedback, PartUsed, WorkReport

from .forms import (
    AddHelperForm, AssignTicketForm, BlockTaskForm, BrandCreateForm, CloseTaskForm, CloseTicketForm, ConductAreaCreateForm,
    CorrectEventTimeForm, CountryCreateForm,
    CreateTechnicianLoginForm, DeactivateTechnicianForm, DismissTicketForm, ExistingAssetOutcomeForm, MarkUnavailableForm, MyProfileForm,
    NegligenceFlagForm, NewAssetForm, PauseTaskForm, RemoveAssignmentForm, ReviewLevelForm, SelfRateLevelForm, SelfRateSkillForm, SetLeadForm,
    SkillCreateForm, StaffAttachmentUploadForm, TaskAttachmentUploadForm, TaskCreateForm, TaskEditForm,
    TaskMessageForm, TaskProductForm, TechnicianCreateForm, TechnicianFirstLoginForm,
    TechnicianEditForm, TicketEditForm, TicketInternalNoteForm, TicketLogisticsForm, TicketReplyForm,
)
from .models import (
    CustomerTicket, ScheduleChangeRequest, ShippingCompany, Task, TaskAsset,
    TaskAssignment, TaskAttachment, TaskEvent, TaskMessage, TaskMessageRecipient, TaskNotification,
    TaskProduct, TicketInternalNote, TicketNotification, TicketReply,
)

TASK_NUMBER_CREATE_ATTEMPTS = 5
WEEK_LENGTH = 7

# Avoids characters easy to mis-type or mis-read out loud/over chat: 0/O, 1/l/I.
# Same alphabet customers.views uses for the same reason.
TEMPORARY_PASSWORD_ALPHABET = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789'


def _generate_temporary_password():
    return get_random_string(12, allowed_chars=TEMPORARY_PASSWORD_ALPHABET)

# A new technician works as helper, alongside a supervisor, until
# certified — the target is to get there within this many days.
NINETY_DAY_TRACK_DAYS = 90

# A rate (on-time %, acceptance latency) built on fewer completed tasks
# than this is noise dressed up as a score — hide it, not show a
# misleading number. Plain counts are shown regardless of volume.
RELIABILITY_MIN_SAMPLE = 20

OPEN_STATUSES = [
    Task.Status.NEW,
    Task.Status.ASSIGNED,
    Task.Status.ACCEPTED,
    Task.Status.IN_PROGRESS,
    Task.Status.PENDING_SUPERVISOR_REVIEW,
    Task.Status.COMPLETED,
    Task.Status.BLOCKED,
]

# "Blocked once work starts. After in_progress, handover means closing the
# task and raising a new one" (docs/database_design_v2.md, task_assignment).
ASSIGNMENT_LOCKED_STATUSES = {
    Task.Status.IN_PROGRESS,
    Task.Status.PENDING_SUPERVISOR_REVIEW,
    Task.Status.COMPLETED,
    Task.Status.CLOSED,
    Task.Status.CANCELLED,
}

# Once a lead is (re)assigned, an in-progress task pipeline restarts at
# "assigned" — the new lead has not accepted yet. Statuses outside this set
# (e.g. blocked) are left alone.
STATUSES_RESET_BY_ASSIGNMENT = {Task.Status.NEW, Task.Status.ASSIGNED, Task.Status.ACCEPTED}

# The lead's button taps, in order. "en_route" and "arrived" don't move
# task.status — only "start" does; from there, filing the report (see
# my_report_form) marks it completed (or pending_supervisor_review, if
# a technician — not a supervisor — filed it), and a manager approving
# it from task detail is what actually closes it — there's no separate
# "complete" tap of its own.
TECHNICIAN_ACTIONS = {
    'accept': (TaskEvent.EventType.ACCEPTED, Task.Status.ACCEPTED),
    'en_route': (TaskEvent.EventType.EN_ROUTE, None),
    'arrive': (TaskEvent.EventType.ARRIVED, None),
    'start': (TaskEvent.EventType.STARTED, Task.Status.IN_PROGRESS),
}
BLOCKABLE_STATUSES = {Task.Status.ACCEPTED, Task.Status.IN_PROGRESS}

# The lead can file a report once work is underway, and correct it while
# it's awaiting a supervisor's or manager's approval. Once the task is
# closed, the approved report is a record — only a manager or admin
# corrects it then (task_report_correct).
REPORT_EDITABLE_STATUSES = {
    Task.Status.IN_PROGRESS, Task.Status.PENDING_SUPERVISOR_REVIEW, Task.Status.COMPLETED,
}
MANAGER_REPORT_CORRECTABLE_STATUSES = {
    Task.Status.PENDING_SUPERVISOR_REVIEW, Task.Status.COMPLETED, Task.Status.CLOSED,
}
# Once the job is finished, its product lines are a record — a supervisor
# can fix their own mistakes while the task is open, but after this only
# the manager tier can change them.
PRODUCTS_LOCKED_STATUSES = {Task.Status.COMPLETED, Task.Status.CLOSED, Task.Status.CANCELLED}
EXISTING_ASSET_ROWS = 4
NEW_ASSET_ROWS = 4
PART_ROWS = 5
TASK_PRODUCT_ROWS = 5

PRIORITY_RANK = Case(
    When(priority=Task.Priority.EMERGENCY, then=Value(0)),
    When(priority=Task.Priority.HIGH, then=Value(1)),
    When(priority=Task.Priority.NORMAL, then=Value(2)),
    When(priority=Task.Priority.LOW, then=Value(3)),
    default=Value(4),
    output_field=IntegerField(),
)


def _next_task_number(country):
    prefix = f'{country.task_prefix or country.iso_code}-'
    count = Task.objects.filter(task_number__startswith=prefix).count()
    return f'{prefix}{count + 1:04d}'


def _parse_datetime_local(value):
    """A plain POST value from an <input type="datetime-local">, naive
    (no timezone) — used where there's no ModelForm already handling
    it, e.g. a proposed schedule_change_request time.
    """
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M')
    except (TypeError, ValueError):
        return None


def _save_new_task(task):
    """Assign a task_number and save, retrying on a rare numbering collision."""
    country = task.site.customer.country
    for _attempt in range(TASK_NUMBER_CREATE_ATTEMPTS):
        task.task_number = _next_task_number(country)
        try:
            with transaction.atomic():
                task.save()
            return
        except IntegrityError:
            continue
    raise IntegrityError('Could not generate a unique task number')


def _next_ticket_number(country):
    prefix = f'{country.task_prefix or country.iso_code}-T'
    count = CustomerTicket.objects.filter(ticket_number__startswith=prefix).count()
    return f'{prefix}{count + 1:04d}'


def save_new_ticket(ticket):
    """Assign a ticket_number and save, retrying on a rare numbering
    collision — same pattern as _save_new_task. Public (no leading
    underscore): customers.views.portal_ticket_new, a different app,
    calls this directly rather than duplicating the retry logic.
    """
    for _attempt in range(TASK_NUMBER_CREATE_ATTEMPTS):
        ticket.ticket_number = _next_ticket_number(ticket.country)
        try:
            with transaction.atomic():
                ticket.save()
            return
        except IntegrityError:
            continue
    raise IntegrityError('Could not generate a unique ticket number')


def _assignment_candidates(task, exclude_ids):
    """Active technicians in the task's country, not already on the task."""
    return Technician.objects.filter(
        is_active=True, country=task.site.customer.country,
    ).exclude(pk__in=exclude_ids).order_by('full_name')


def _candidates_with_skill_level(candidates_qs, task):
    """Materialize candidates, annotating each with its level for the task's required skill."""
    candidates = list(candidates_qs)
    levels = {}
    if task.required_skill_id:
        levels = dict(
            TechnicianSkill.objects.filter(
                skill_id=task.required_skill_id, technician__in=candidates,
            ).values_list('technician_id', 'level'),
        )
    for technician in candidates:
        technician.skill_level = levels.get(technician.pk)
    return candidates


def _technicians_with_next_scheduled_task(technicians):
    """Annotate each technician with `.next_scheduled_task` — their
    soonest upcoming open, scheduled assignment, or None — so whoever is
    assigning a lead/helper can see a conflict right here instead of
    checking the week board separately.
    """
    technicians = list(technicians)
    upcoming = TaskAssignment.objects.filter(
        technician__in=technicians, is_active=True, task__status__in=OPEN_STATUSES,
        task__scheduled_for__gte=timezone.now(),
    ).select_related('task__site__customer').order_by('task__scheduled_for')
    next_by_technician = {}
    for assignment in upcoming:
        next_by_technician.setdefault(assignment.technician_id, assignment.task)
    for technician in technicians:
        technician.next_scheduled_task = next_by_technician.get(technician.pk)
    return technicians


def _supervisors_with_next_scheduled_task(supervisors):
    """Same as _technicians_with_next_scheduled_task, but for whoever is
    picked as a task's responsible_supervisor — that's a direct FK on
    Task, not a TaskAssignment row, so the lookup is simpler.
    """
    supervisors = list(supervisors)
    upcoming = Task.objects.filter(
        responsible_supervisor__in=supervisors, status__in=OPEN_STATUSES, scheduled_for__gte=timezone.now(),
    ).select_related('site__customer').order_by('scheduled_for')
    next_by_supervisor = {}
    for task in upcoming:
        next_by_supervisor.setdefault(task.responsible_supervisor_id, task)
    for supervisor in supervisors:
        supervisor.next_scheduled_task = next_by_supervisor.get(supervisor.pk)
    return supervisors


def _skills_with_current_rating(technician):
    """Every active skill, each annotated with `.current` — the
    technician's TechnicianSkill row for it, or None if never rated.
    """
    skills = list(Skill.objects.filter(is_active=True))
    current_by_skill_id = {
        rating.skill_id: rating
        for rating in TechnicianSkill.objects.filter(
            technician=technician, skill__in=skills,
        ).select_related('set_by')
    }
    for skill in skills:
        skill.current = current_by_skill_id.get(skill.id)
    return skills


def _split_by_category(skills):
    """Basic-level skills first, then cardio — the two groups the
    certification bar itself already treats differently (see
    _certification_status), shown as separate sections everywhere a
    technician's skill list renders.
    """
    basic = [skill for skill in skills if skill.category == Skill.Category.OTHER]
    cardio = [skill for skill in skills if skill.category == Skill.Category.CARDIO]
    return basic, cardio


def _conduct_areas_with_current_rating(technician):
    """Every active conduct area, each annotated with `.current` the same way."""
    areas = list(ConductArea.objects.filter(is_active=True).order_by('name'))
    current_by_area_id = {
        rating.conduct_area_id: rating
        for rating in TechnicianConduct.objects.filter(
            technician=technician, conduct_area__in=areas,
        ).select_related('set_by')
    }
    for area in areas:
        area.current = current_by_area_id.get(area.id)
    return areas


def _certification_status(technician):
    """Confirmed-only progress toward the "reliable technician" bar: level
    >= RELIABLE_LEVEL on every non-cardio skill and every conduct area,
    counting only supervisor-confirmed ratings — a self-rating never counts
    on its own (docs/database_design_v2.md, §3). Cardio (Advanced-level)
    skills are excluded from the bar; clearing one just marks readiness in
    that additional skill set, tracked separately as `cardio_ready`.
    """
    confirmed_skill_levels = dict(
        TechnicianSkill.objects.filter(
            technician=technician, source=TechnicianSkill.Source.SUPERVISOR,
        ).values_list('skill_id', 'level'),
    )
    confirmed_conduct_levels = dict(
        TechnicianConduct.objects.filter(
            technician=technician, source=TechnicianConduct.Source.SUPERVISOR,
        ).values_list('conduct_area_id', 'level'),
    )

    non_cardio_ids = list(
        Skill.objects.filter(is_active=True, category=Skill.Category.OTHER).values_list('id', flat=True),
    )
    cardio_ids = list(
        Skill.objects.filter(is_active=True, category=Skill.Category.CARDIO).values_list('id', flat=True),
    )
    conduct_ids = list(ConductArea.objects.filter(is_active=True).values_list('id', flat=True))

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
    technicians = Technician.objects.filter(
        is_active=True, country=country, role=Technician.Role.TECHNICIAN,
    ).order_by('full_name')
    return sorted(
        ((t, _confirmed_points(t)) for t in technicians),
        key=lambda pair: pair[1], reverse=True,
    )


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


def _with_lead_prefetch(queryset):
    """Attach each task's active lead assignment as `.lead_assignments`, for _attach_lead_technician."""
    return queryset.prefetch_related(
        Prefetch(
            'assignments',
            queryset=TaskAssignment.objects.filter(
                role=TaskAssignment.Role.LEAD, is_active=True,
            ).select_related('technician'),
            to_attr='lead_assignments',
        ),
    )


def _attach_lead_technician(tasks):
    """Set `.lead_technician` on each task from the `_with_lead_prefetch` prefetch."""
    for task in tasks:
        leads = task.lead_assignments
        task.lead_technician = leads[0].technician if leads else None




def _week_window(request):
    """The (today, start, end) of the 7-day window from ?start=, default this week's Monday."""
    today = timezone.localtime().date()
    start = parse_date(request.GET.get('start', '') or '') or (today - timedelta(days=today.weekday()))
    end = start + timedelta(days=WEEK_LENGTH - 1)
    return today, start, end


def _week_nav_context(today, start):
    return {
        'today': today,
        'start': start,
        'prev_start': start - timedelta(days=WEEK_LENGTH),
        'next_start': start + timedelta(days=WEEK_LENGTH),
        'this_week_start': today - timedelta(days=today.weekday()),
    }


def _next_technician_action(task):
    """Which button the lead should see next, or None if nothing is theirs to tap right now."""
    if task.status == Task.Status.ASSIGNED:
        return 'accept'
    if task.status == Task.Status.ACCEPTED:
        seen = set(task.events.filter(
            event_type__in=[TaskEvent.EventType.EN_ROUTE, TaskEvent.EventType.ARRIVED],
        ).values_list('event_type', flat=True))
        if TaskEvent.EventType.ARRIVED in seen:
            return 'start'
        if TaskEvent.EventType.EN_ROUTE in seen:
            return 'arrive'
        return 'en_route'
    return None


# A lead's own mistaken tap can be undone by them for this long — after
# that, a manager or admin corrects the time instead (correct_event_time).
UNDO_WINDOW = timedelta(minutes=10)
UNDOABLE_TAPS = {
    TaskEvent.EventType.ACCEPTED, TaskEvent.EventType.EN_ROUTE, TaskEvent.EventType.ARRIVED,
    TaskEvent.EventType.STARTED, TaskEvent.EventType.PAUSED, TaskEvent.EventType.RESUMED,
}
# Undoing these moves the status back; the others never changed it.
UNDO_STATUS = {
    TaskEvent.EventType.ACCEPTED: (Task.Status.ACCEPTED, Task.Status.ASSIGNED),
    TaskEvent.EventType.STARTED: (Task.Status.IN_PROGRESS, Task.Status.ACCEPTED),
}
CORRECTABLE_EVENTS = UNDOABLE_TAPS | {TaskEvent.EventType.COMPLETED}


def undoable_tap(task, user):
    """The lead's own last tap, if it's still theirs to undo: the most
    recent event on the task, one of theirs, under 10 minutes old, and the
    task still in the status that tap left it in. Otherwise None.
    """
    last = task.events.order_by('-occurred_at', '-pk').first()
    if (
        last is None or last.event_type not in UNDOABLE_TAPS or last.actor_id != user.pk
        or last.corrected_at or timezone.now() - last.occurred_at > UNDO_WINDOW
    ):
        return None
    if last.event_type in UNDO_STATUS and task.status != UNDO_STATUS[last.event_type][0]:
        return None
    return last


def undo_last_tap(task, user):
    """Removes the lead's own mistaken tap and puts the status back.
    Shared by the web task screen and the mobile API. False if there's
    nothing they can undo.
    """
    with transaction.atomic():
        event = undoable_tap(task, user)
        if event is None:
            return False
        if event.event_type in UNDO_STATUS:
            task.status = UNDO_STATUS[event.event_type][1]
            task.save(update_fields=['status'])
        event.delete()
    return True


def _correction_window(event):
    """The earliest/latest a corrected time for this event may be — after
    the event before it and before the event after it, each at its own
    effective (corrected or original) time.
    """
    events = list(event.task.events.order_by('occurred_at', 'pk'))
    index = next(i for i, e in enumerate(events) if e.pk == event.pk)
    earliest = events[index - 1].effective_at if index > 0 else None
    latest = events[index + 1].effective_at if index + 1 < len(events) else None
    return earliest, latest


def _task_is_paused(task):
    """Stopped for the day and not yet resumed — the most recent of
    started/paused/resumed decides, since a multi-day task cycles
    through paused/resumed any number of times before it's completed.
    """
    last = task.events.filter(
        event_type__in=[TaskEvent.EventType.STARTED, TaskEvent.EventType.PAUSED, TaskEvent.EventType.RESUMED],
    ).order_by('-occurred_at').values_list('event_type', flat=True).first()
    return last == TaskEvent.EventType.PAUSED


def _task_message_recipients(task):
    """Everyone a task message should notify — whoever's actually on the
    job right now (active lead, active helpers, the responsible
    supervisor), whoever created it, and every manager-tier person in
    its country, so the people running the country stay in the loop on
    every job, not just the ones they happen to open. A set, since more
    than one of these can be the same person (a manager who's also the
    responsible supervisor, say) — each still gets exactly one row.
    """
    country = task.site.customer.country
    recipients = set(
        Technician.objects.filter(
            assignments__task=task, assignments__is_active=True,
        ),
    )
    if task.responsible_supervisor_id:
        recipients.add(task.responsible_supervisor)
    created_by_technician = getattr(task.created_by, 'technician', None)
    if created_by_technician is not None:
        recipients.add(created_by_technician)
    recipients.update(
        Technician.objects.filter(
            country=country, role__in=[Technician.Role.MANAGER, Technician.Role.ADMIN], is_active=True,
        ),
    )
    return recipients


def _post_task_message(request, task, text):
    """Posts a message and fans it out to everyone _task_message_recipients
    names, except whoever just sent it — they don't need to be told about
    their own message.
    """
    message = TaskMessage.objects.create(task=task, sent_by=request.user, message=text, sent_at=timezone.now())
    sender_technician = getattr(request.user, 'technician', None)
    recipients = _task_message_recipients(task)
    recipients.discard(sender_technician)
    TaskMessageRecipient.objects.bulk_create([
        TaskMessageRecipient(message=message, technician=recipient) for recipient in recipients
    ])
    return message


def _attachment_media_type(uploaded_file):
    content_type = uploaded_file.content_type or ''
    if content_type.startswith('image/'):
        return TaskAttachment.MediaType.PHOTO
    if content_type.startswith('video/'):
        return TaskAttachment.MediaType.VIDEO
    return TaskAttachment.MediaType.DOCUMENT


def _save_attachment(request, task, uploaded_file, purpose, source=TaskAttachment.Source.TECHNICIAN):
    path = default_storage.save(f'attachments/{task.pk}/{uploaded_file.name}', uploaded_file)
    TaskAttachment.objects.create(
        task=task, storage_kind=TaskAttachment.StorageKind.FILE,
        url=request.build_absolute_uri(default_storage.url(path)),
        media_type=_attachment_media_type(uploaded_file), purpose=purpose,
        source=source, uploaded_by=request.user, uploaded_at=timezone.now(),
    )


def _send_notification_email(subject, text_body, template_name, context, recipient_list, attachment=None):
    """Every outbound notification email in this app goes through here
    — a plain-text body for clients that don't render HTML, and a
    matching styled HTML alternative (tasks/templates/tasks/email/)
    for everyone else. Always fail-silent, same as every caller
    already was: a failed send must never block the action that
    triggered it.

    attachment, when given, is a real file on the ticket/task itself
    (e.g. a quotation) — attached as-is, not just linked.

    The logo is embedded inline (a cid: reference, not a plain URL) —
    this renders outside any request, and an email client fetches remote
    images itself, from wherever it's reading the mail, which may have
    no route at all to a local dev server. Embedding the actual bytes in
    the message is the only way it reliably shows up everywhere.
    """
    html_body = render_to_string(template_name, {**context, 'logo_url': 'cid:logo'})
    email = EmailMultiAlternatives(
        subject=subject, body=text_body, from_email=None, to=recipient_list,
    )
    email.attach_alternative(html_body, 'text/html')
    email.mixed_subtype = 'related'
    logo_path = find_static_file('images/email_header.png')
    if logo_path:
        with open(logo_path, 'rb') as logo_file:
            logo_image = MIMEImage(logo_file.read())
        logo_image.add_header('Content-ID', '<logo>')
        logo_image.add_header('Content-Disposition', 'inline', filename='email_header.png')
        email.attach(logo_image)
    if attachment:
        attachment.open('rb')
        try:
            email.attach(attachment.name.rsplit('/', 1)[-1], attachment.read())
        finally:
            attachment.close()
    email.send(fail_silently=True)


def _send_schedule_notification(task):
    """Best-effort, and forced to English — same reasoning as
    reports._send_feedback_email: a failed send shouldn't block the
    supervisor's flow, and there's nowhere to read a customer's language
    preference from. Manual every time; nothing calls this on its own.
    """
    country_tz = ZoneInfo(task.site.customer.country.timezone)
    local_time = timezone.localtime(task.scheduled_for, country_tz)
    contact = task.site.effective_contact_name or task.site.customer.name
    site = task.site.name
    date = local_time.strftime('%B %d, %Y')
    time = local_time.strftime('%I:%M %p').lstrip('0')
    with translation.override('en'):
        subject = _('Confirming your upcoming Profit Sports Solutions visit — %(number)s') % {
            'number': task.task_number,
        }
        message = _(
            'Dear %(contact)s,\n\n'
            'This confirms our technician is scheduled to visit %(site)s on %(date)s at %(time)s.\n\n'
            "If this time doesn't work for you, please contact us to reschedule.\n\n"
            'Best regards,\n'
            'Profit Sports Solutions\n',
        ) % {'contact': contact, 'site': site, 'date': date, 'time': time}
    _send_notification_email(
        subject, message, 'tasks/email/schedule_notification.html',
        {'contact': contact, 'site': site, 'date': date, 'time': time},
        [task.site.effective_contact_email],
    )


def _send_delay_notice(task, reason):
    """A different message from _send_schedule_notification: that one
    confirms a plan, this one apologizes for one slipping — traffic, a
    previous job running long, and so on. Always manual and always
    needs a reason, since there's no automatic way to know the team is
    running behind.
    """
    contact = task.site.effective_contact_name or task.site.customer.name
    site = task.site.name
    with translation.override('en'):
        subject = _('A short delay for your Profit Sports Solutions visit — %(number)s') % {
            'number': task.task_number,
        }
        message = _(
            'Dear %(contact)s,\n\n'
            'Our technician for %(site)s is running behind schedule today: %(reason)s\n\n'
            "We're sorry for the inconvenience and will be there as soon as we can.\n\n"
            'Best regards,\n'
            'Profit Sports Solutions\n',
        ) % {'contact': contact, 'site': site, 'reason': reason}
    _send_notification_email(
        subject, message, 'tasks/email/delay_notice.html',
        {'contact': contact, 'site': site, 'reason': reason},
        [task.site.effective_contact_email],
    )


# Public tracking-lookup URLs, one per carrier that has a stable one —
# {tracking} is the only placeholder, filled in URL-encoded. Local
# courier and Other have no universal page, so they're left out on
# purpose; the email still names the carrier either way, just without
# a link.
CARRIER_TRACKING_URLS = {
    ShippingCompany.DHL: 'https://www.dhl.com/en/express/tracking.html?AWB={tracking}',
    ShippingCompany.FEDEX: 'https://www.fedex.com/fedextrack/?trknbr={tracking}',
    ShippingCompany.UPS: 'https://www.ups.com/track?tracknum={tracking}',
    ShippingCompany.ARAMEX: 'https://www.aramex.com/track/results?ShipmentNumber={tracking}',
    ShippingCompany.TNT: 'https://www.tnt.com/express/en_us/site/shipping-tools/tracking.html?cons={tracking}',
}


def _send_shipping_notice(contact_name, entity_name, reference, company, tracking_number, contact_email):
    """Shared by task detail and ticket review — the only two places a
    shipping_tracking_number can live. Only the carrier and tracking
    number go to the customer; pak_reference_number is internal and
    never sent. `company` is the raw ShippingCompany code (or ''), not
    its display label — needed as-is to look up a tracking URL.
    """
    carrier_label = ShippingCompany(company).label if company else ''
    tracking_url = None
    if company:
        url_template = CARRIER_TRACKING_URLS.get(company)
        if url_template:
            tracking_url = url_template.format(tracking=quote(tracking_number))
    with translation.override('en'):
        subject = _('Your shipment is on its way — %(reference)s') % {'reference': reference}
        lines = [_('Tracking number: %(tracking)s') % {'tracking': tracking_number}]
        if company:
            lines.insert(0, _('Carrier: %(company)s') % {'company': carrier_label})
            if tracking_url:
                lines.append(_('Track it here: %(url)s') % {'url': tracking_url})
        tracking_lines = '\n'.join(lines)
        message = _(
            'Dear %(contact)s,\n\n'
            'Good news — your shipment for %(entity)s is on its way. '
            'You can track it using the details below.\n\n'
            '%(tracking_lines)s\n\n'
            'Best regards,\n'
            'Profit Sports Solutions\n',
        ) % {'contact': contact_name, 'entity': entity_name, 'tracking_lines': tracking_lines}
    _send_notification_email(
        subject, message, 'tasks/email/shipping_notice.html',
        {
            'contact': contact_name, 'entity': entity_name, 'carrier_label': carrier_label,
            'tracking_number': tracking_number, 'tracking_url': tracking_url,
        },
        [contact_email],
    )


def _send_shipment_notice_to_supervisor(request, task):
    """Most shipments arrive against a factory tracking number, not a
    customer request — the responsible_supervisor is the one staffing
    the task and the one who actually needs to know parts are on the
    way, so this fires whenever shipping_tracking_number changes,
    separately from _send_shipping_notice above (which is the
    customer-facing one, manual, and about a different audience
    entirely). Best-effort, and only when there's a responsible
    supervisor with a login and an email on file — same guard
    _send_reply_to_staff uses for the same reason.
    """
    supervisor = task.responsible_supervisor
    if not supervisor or not supervisor.user_id or not supervisor.user.email:
        return
    link = request.build_absolute_uri(reverse('tasks:task_detail', args=[task.pk]))
    carrier_label = task.get_shipping_company_display() if task.shipping_company else ''
    tracking_url = None
    if task.shipping_company:
        url_template = CARRIER_TRACKING_URLS.get(task.shipping_company)
        if url_template:
            tracking_url = url_template.format(tracking=quote(task.shipping_tracking_number))
    with translation.override('en'):
        subject = _('Shipment on the way — %(task_number)s') % {'task_number': task.task_number}
        if task.shipping_company:
            tracking_line = _('Carrier: %(company)s — tracking number %(tracking)s') % {
                'company': carrier_label, 'tracking': task.shipping_tracking_number,
            }
        else:
            tracking_line = _('Tracking number: %(tracking)s') % {'tracking': task.shipping_tracking_number}
        message = _(
            'A shipment for %(task_number)s (%(site)s) now has a tracking number.\n\n'
            '%(tracking_line)s\n\n'
            'View the task here:\n%(link)s\n',
        ) % {
            'task_number': task.task_number, 'site': task.site.name,
            'tracking_line': tracking_line, 'link': link,
        }
    _send_notification_email(
        subject, message, 'tasks/email/shipment_notice_supervisor.html',
        {
            'task_number': task.task_number, 'site': task.site.name, 'carrier_label': carrier_label,
            'tracking_number': task.shipping_tracking_number, 'tracking_url': tracking_url, 'link': link,
        },
        [supervisor.user.email],
    )


def _send_feedback_email(request, feedback):
    """Best-effort — a failed send shouldn't stop the supervisor's flow or
    leave them staring at a 500. EMAIL_BACKEND defaults to the console in
    dev, so this always "succeeds" locally.

    Forced to English regardless of who sends it: there's nowhere to read a
    customer's language preference from, and without this the email would
    silently follow whichever language the supervisor's own UI happens to
    be in — not the customer's.
    """
    task = feedback.task
    country_tz = ZoneInfo(task.site.customer.country.timezone)
    link = request.build_absolute_uri(reverse('reports:feedback_form', args=[feedback.token]))
    contact = task.site.effective_contact_name or task.site.customer.name
    site = task.site.name
    with translation.override('en'):
        visit_date = timezone.localtime(task.report.submitted_at, country_tz).date().strftime('%B %d, %Y')
        subject = _("We'd love your feedback on your recent Profit Sports Solutions visit")
        message = _(
            'Dear %(contact)s,\n\n'
            'Thank you for choosing Profit Sports Solutions. We completed a service visit at '
            '%(site)s on %(date)s, and would greatly appreciate a moment of your time to share '
            'your feedback.\n\n'
            '%(link)s\n\n'
            'Your feedback helps us maintain the standard of service you expect from us.\n\n'
            'Best regards,\n'
            'Profit Sports Solutions\n',
        ) % {'contact': contact, 'site': site, 'date': visit_date, 'link': link}
    _send_notification_email(
        subject, message, 'tasks/email/feedback_request.html',
        {'contact': contact, 'site': site, 'date': visit_date, 'link': link},
        [task.site.effective_contact_email],
    )


@login_required
def dashboard(request):
    """At a glance: who's available today, and every open task with its
    lead and schedule. Every role has view_dashboard (see migration
    0024_dashboard_is_the_default_landing_page) since this is now the
    landing page every login lands on — spots.views.home.
    """
    require_permission(request, RolePermission.Permission.VIEW_DASHBOARD)
    active_country = get_active_country(request)

    technicians = list(Technician.objects.filter(
        is_active=True, country=active_country, role=Technician.Role.TECHNICIAN,
    ).order_by('full_name'))
    active_task_counts = dict(
        TaskAssignment.objects.filter(
            technician__in=technicians, is_active=True, task__status__in=OPEN_STATUSES,
        ).values('technician_id').annotate(count=Count('id')).values_list('technician_id', 'count'),
    )
    for technician in technicians:
        technician.active_task_count = active_task_counts.get(technician.id, 0)

    tasks = list(_with_lead_prefetch(
        Task.objects.filter(status__in=OPEN_STATUSES, site__customer__country=active_country)
        .select_related('site__customer', 'task_type')
        .annotate(priority_rank=PRIORITY_RANK).order_by('priority_rank', 'scheduled_for', 'promised_at'),
    ))
    _attach_lead_technician(tasks)

    # The numbers a supervisor actually opens this screen to see, before
    # the two full lists below: who's deployable right now, and which
    # open tasks need attention first.
    available_count = sum(1 for t in technicians if t.is_available)
    stats = {
        'available_count': available_count,
        'unavailable_count': len(technicians) - available_count,
        'open_task_count': len(tasks),
        'unassigned_count': sum(1 for t in tasks if t.lead_technician is None),
        'emergency_count': sum(1 for t in tasks if t.priority == Task.Priority.EMERGENCY),
    }

    context = {
        'technicians': technicians,
        'tasks': tasks,
        'stats': stats,
    }
    return render(request, 'tasks/dashboard.html', context)


@login_required
def task_list(request):
    require_permission(request, RolePermission.Permission.VIEW_TASKS)
    active_country = get_active_country(request)

    status = request.GET.get('status', 'open')
    task_id = request.GET.get('task_id', '').strip()
    site = request.GET.get('site', '').strip()
    pak_reference = request.GET.get('pak_reference', '').strip()
    serial_number = request.GET.get('serial_number', '').strip()
    customer_id = request.GET.get('customer', '')
    technician_id = request.GET.get('technician', '')
    shipping_company = request.GET.get('shipping_company', '')
    scheduled_from = parse_date(request.GET.get('scheduled_from', '') or '')
    scheduled_to = parse_date(request.GET.get('scheduled_to', '') or '')

    tasks = _with_lead_prefetch(
        Task.objects.filter(site__customer__country=active_country).select_related('site__customer', 'task_type'),
    )

    if status == 'open':
        tasks = tasks.filter(status__in=OPEN_STATUSES)
    elif status != 'all':
        tasks = tasks.filter(status=status)

    if task_id:
        tasks = tasks.filter(task_number__icontains=task_id)
    if site:
        tasks = tasks.filter(Q(site__name__icontains=site) | Q(site__address__icontains=site)).distinct()
    if pak_reference:
        tasks = tasks.filter(pak_reference_number__icontains=pak_reference)
    if serial_number:
        tasks = tasks.filter(
            Q(task_assets__asset__serial_no__icontains=serial_number)
            | Q(products__serial_number__icontains=serial_number)
        ).distinct()

    if customer_id:
        tasks = tasks.filter(site__customer_id=customer_id)

    if technician_id:
        tasks = tasks.filter(
            assignments__technician_id=technician_id, assignments__is_active=True,
        ).distinct()

    if shipping_company:
        tasks = tasks.filter(shipping_company=shipping_company)

    if scheduled_from:
        tasks = tasks.filter(scheduled_for__date__gte=scheduled_from)
    if scheduled_to:
        tasks = tasks.filter(scheduled_for__date__lte=scheduled_to)

    tasks = tasks.annotate(priority_rank=PRIORITY_RANK).order_by('priority_rank', 'promised_at')

    paginator = Paginator(tasks, 25)
    page_obj = paginator.get_page(request.GET.get('page'))
    _attach_lead_technician(page_obj)

    # One combined query string for every non-status filter, so the status
    # tabs and pagination links carry the filters forward instead of
    # silently dropping them.
    filter_params = {
        key: value for key, value in {
            'task_id': task_id, 'site': site, 'pak_reference': pak_reference,
            'serial_number': serial_number, 'customer': customer_id, 'technician': technician_id,
            'shipping_company': shipping_company,
            'scheduled_from': request.GET.get('scheduled_from', ''),
            'scheduled_to': request.GET.get('scheduled_to', ''),
        }.items() if value
    }

    context = {
        'page_obj': page_obj,
        'status': status,
        'task_id': task_id,
        'site': site,
        'pak_reference': pak_reference,
        'serial_number': serial_number,
        'status_choices': Task.Status.choices,
        'customers': Customer.objects.filter(is_active=True, country=active_country).order_by('name'),
        'technicians': Technician.objects.filter(is_active=True, country=active_country).order_by('full_name'),
        'shipping_company_choices': ShippingCompany.choices,
        'selected_customer': customer_id,
        'selected_technician': technician_id,
        'selected_shipping_company': shipping_company,
        'scheduled_from': request.GET.get('scheduled_from', ''),
        'scheduled_to': request.GET.get('scheduled_to', ''),
        'filter_qs': urlencode(filter_params),
    }
    return render(request, 'tasks/task_list.html', context)


@login_required
def all_tasks(request):
    """Every task in every country, for a manager auditing or tracking
    something that isn't scoped to whichever country they last switched
    to — a separate, independent field for each thing you'd actually
    search a task by (task ID, customer, site, PAK reference, date,
    country), instead of one combined box guessing which of those you
    meant. Manager-only:
    this is a global, cross-country view, the same fixed floor as
    skill_list and role_permissions.
    """
    require_manager(request)

    status = request.GET.get('status', 'open')
    task_id = request.GET.get('task_id', '').strip()
    customer = request.GET.get('customer', '').strip()
    site = request.GET.get('site', '').strip()
    pak_reference = request.GET.get('pak_reference', '').strip()
    serial_number = request.GET.get('serial_number', '').strip()
    date = parse_date(request.GET.get('date', '') or '')
    country_id = request.GET.get('country', '')

    tasks = _with_lead_prefetch(
        Task.objects.select_related('site__customer__country', 'task_type'),
    )

    if status == 'open':
        tasks = tasks.filter(status__in=OPEN_STATUSES)
    elif status != 'all':
        tasks = tasks.filter(status=status)

    if task_id:
        tasks = tasks.filter(task_number__icontains=task_id)
    if customer:
        tasks = tasks.filter(site__customer__name__icontains=customer)
    if site:
        tasks = tasks.filter(Q(site__name__icontains=site) | Q(site__address__icontains=site)).distinct()
    if pak_reference:
        tasks = tasks.filter(pak_reference_number__icontains=pak_reference)
    if serial_number:
        tasks = tasks.filter(
            Q(task_assets__asset__serial_no__icontains=serial_number)
            | Q(products__serial_number__icontains=serial_number)
        ).distinct()
    if date:
        tasks = tasks.filter(scheduled_for__date=date)
    if country_id:
        tasks = tasks.filter(site__customer__country_id=country_id)

    tasks = tasks.annotate(priority_rank=PRIORITY_RANK).order_by('priority_rank', 'promised_at')

    paginator = Paginator(tasks, 25)
    page_obj = paginator.get_page(request.GET.get('page'))
    _attach_lead_technician(page_obj)

    filter_params = {
        key: value for key, value in {
            'task_id': task_id, 'customer': customer, 'site': site,
            'pak_reference': pak_reference, 'serial_number': serial_number,
            'date': request.GET.get('date', ''), 'country': country_id,
        }.items() if value
    }

    context = {
        'page_obj': page_obj,
        'status': status,
        'task_id': task_id,
        'customer': customer,
        'site': site,
        'pak_reference': pak_reference,
        'serial_number': serial_number,
        'date': request.GET.get('date', ''),
        'status_choices': Task.Status.choices,
        'countries': Country.objects.order_by('name'),
        'selected_country': country_id,
        'filter_qs': urlencode(filter_params),
    }
    return render(request, 'tasks/all_tasks.html', context)


def can_supervisor_approve(technician, task):
    """The task's own responsible_supervisor signs off on a technician's
    report before it ever reaches a manager; a manager can also do this
    themselves (a superset, same as everywhere else), which matters if no
    supervisor is set yet.
    """
    return technician.is_manager_tier or technician.id == task.responsible_supervisor_id


def approve_report_as_supervisor(task, actor):
    """Pending supervisor review -> completed (awaiting the manager).
    False if there's no report waiting on a supervisor. Shared by the web
    task detail and the mobile API; the caller checks permission.
    """
    if task.status != Task.Status.PENDING_SUPERVISOR_REVIEW:
        return False
    task.status = Task.Status.COMPLETED
    task.save(update_fields=['status'])
    TaskEvent.objects.create(
        task=task, event_type=TaskEvent.EventType.SUPERVISOR_APPROVED, occurred_at=timezone.now(), actor=actor,
    )
    return True


def approve_report_as_manager(task, actor):
    """Completed -> closed. False if there's no report waiting on a
    manager. The caller checks the manager tier.
    """
    if task.status != Task.Status.COMPLETED:
        return False
    task.status = Task.Status.CLOSED
    task.save(update_fields=['status'])
    TaskEvent.objects.create(
        task=task, event_type=TaskEvent.EventType.REPORT_APPROVED, occurred_at=timezone.now(), actor=actor,
    )
    return True


@login_required
def task_detail(request, pk):
    requesting_technician = require_permission(request, RolePermission.Permission.VIEW_TASKS)

    task = _scoped_or_404(
        Task.objects.select_related(
            'site__customer__country', 'task_type', 'brand', 'required_skill', 'created_by', 'report',
        ).prefetch_related('report__parts_used'),
        pk, requesting_technician, get_active_country(request), 'site__customer__country',
    )

    if requesting_technician.is_manager_tier:
        task.notifications.filter(seen_at__isnull=True).update(seen_at=timezone.now())
    TaskMessageRecipient.objects.filter(
        message__task=task, technician=requesting_technician, seen_at__isnull=True,
    ).update(seen_at=timezone.now())

    message_form = TaskMessageForm()
    if request.method == 'POST' and request.POST.get('action') == 'add_message':
        message_form = TaskMessageForm(request.POST)
        if message_form.is_valid():
            _post_task_message(request, task, message_form.cleaned_data['message'])
            messages.success(request, _('Message sent.'))
            return redirect('tasks:task_detail', pk=task.pk)

    if request.method == 'POST' and request.POST.get('action') == 'notify_schedule':
        require_permission(request, RolePermission.Permission.CREATE_TASKS)
        _require_task_owner(request, task)
        if not task.scheduled_for:
            messages.error(request, _('Set a scheduled time before notifying the customer.'))
        elif not task.site.effective_contact_email:
            messages.error(request, _('Add a contact email for this site before notifying the customer.'))
        else:
            _send_schedule_notification(task)
            task.schedule_notified_at = timezone.now()
            task.schedule_notified_by = request.user
            task.save(update_fields=['schedule_notified_at', 'schedule_notified_by'])
            messages.success(request, _('Customer notified of the scheduled visit.'))
        return redirect('tasks:task_detail', pk=task.pk)

    if request.method == 'POST' and request.POST.get('action') == 'set_schedule_time':
        set_time_technician = require_permission(request, RolePermission.Permission.CREATE_TASKS)
        _require_task_owner(request, task)
        if not task.scheduled_date or task.scheduled_for:
            messages.error(request, _('There is no day-only schedule waiting for a time.'))
        else:
            time_value = parse_time(request.POST.get('scheduled_time', ''))
            if not time_value:
                messages.error(request, _('Enter a time.'))
            else:
                task.scheduled_for = timezone.make_aware(datetime.combine(task.scheduled_date, time_value))
                # A supervisor completing a manager's day-only date is the
                # normal path and stays open; a manager doing it themselves
                # locks it, same rule as everywhere else.
                task.schedule_time_locked = set_time_technician.is_manager_tier
                task.save(update_fields=['scheduled_for', 'schedule_time_locked'])
                TaskEvent.objects.create(
                    task=task, event_type=TaskEvent.EventType.RESCHEDULED,
                    occurred_at=timezone.now(), actor=request.user,
                )
                messages.success(request, _('Time set.'))
        return redirect('tasks:task_detail', pk=task.pk)

    if request.method == 'POST' and request.POST.get('action') == 'request_schedule_change':
        require_permission(request, RolePermission.Permission.CREATE_TASKS)
        _require_task_owner(request, task)
        if not task.schedule_time_locked:
            messages.error(request, _('This schedule is not locked — edit it directly instead.'))
        elif task.schedule_change_requests.filter(status=ScheduleChangeRequest.Status.PENDING).exists():
            messages.error(request, _('There is already a pending request for this task.'))
        else:
            requested_for = _parse_datetime_local(request.POST.get('requested_scheduled_for', ''))
            if not requested_for:
                messages.error(request, _('Enter the date and time you are requesting.'))
            else:
                change_request = ScheduleChangeRequest.objects.create(
                    task=task, requested_by=requesting_technician,
                    requested_scheduled_for=timezone.make_aware(requested_for),
                    reason=request.POST.get('reason', '').strip(), created_at=timezone.now(),
                )
                TaskEvent.objects.create(
                    task=task, event_type=TaskEvent.EventType.SCHEDULE_CHANGE_REQUESTED,
                    occurred_at=timezone.now(), actor=request.user, note=change_request.reason,
                )
                messages.success(request, _('Change requested — a manager will review it.'))
        return redirect('tasks:task_detail', pk=task.pk)

    if request.method == 'POST' and request.POST.get('action') == 'approve_schedule_change':
        require_manager(request)
        change_request = get_object_or_404(
            ScheduleChangeRequest, pk=request.POST.get('request_id'), task=task,
            status=ScheduleChangeRequest.Status.PENDING,
        )
        task.scheduled_for = change_request.requested_scheduled_for
        task.scheduled_date = timezone.localtime(task.scheduled_for).date()
        task.schedule_time_locked = True
        task.save(update_fields=['scheduled_for', 'scheduled_date', 'schedule_time_locked'])
        change_request.status = ScheduleChangeRequest.Status.APPROVED
        change_request.reviewed_by = request.user
        change_request.reviewed_at = timezone.now()
        change_request.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
        TaskEvent.objects.create(
            task=task, event_type=TaskEvent.EventType.RESCHEDULED,
            occurred_at=timezone.now(), actor=request.user,
        )
        TaskEvent.objects.create(
            task=task, event_type=TaskEvent.EventType.SCHEDULE_CHANGE_APPROVED,
            occurred_at=timezone.now(), actor=request.user,
        )
        messages.success(request, _('Schedule change approved.'))
        return redirect('tasks:task_detail', pk=task.pk)

    if request.method == 'POST' and request.POST.get('action') == 'deny_schedule_change':
        require_manager(request)
        change_request = get_object_or_404(
            ScheduleChangeRequest, pk=request.POST.get('request_id'), task=task,
            status=ScheduleChangeRequest.Status.PENDING,
        )
        change_request.status = ScheduleChangeRequest.Status.DENIED
        change_request.reviewed_by = request.user
        change_request.reviewed_at = timezone.now()
        change_request.review_note = request.POST.get('review_note', '').strip()
        change_request.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_note'])
        TaskEvent.objects.create(
            task=task, event_type=TaskEvent.EventType.SCHEDULE_CHANGE_DENIED,
            occurred_at=timezone.now(), actor=request.user, note=change_request.review_note,
        )
        messages.success(request, _('Schedule change denied.'))
        return redirect('tasks:task_detail', pk=task.pk)

    if request.method == 'POST' and request.POST.get('action') == 'notify_delay':
        require_permission(request, RolePermission.Permission.CREATE_TASKS)
        _require_task_owner(request, task)
        reason = request.POST.get('delay_reason', '').strip()
        if not task.scheduled_for:
            messages.error(request, _('Set a scheduled time before notifying the customer.'))
        elif not task.site.effective_contact_email:
            messages.error(request, _('Add a contact email for this site before notifying the customer.'))
        elif not reason:
            messages.error(request, _('Explain the reason for the delay before notifying the customer.'))
        else:
            _send_delay_notice(task, reason)
            TaskEvent.objects.create(
                task=task, event_type=TaskEvent.EventType.DELAY_NOTICE,
                occurred_at=timezone.now(), actor=request.user, note=reason,
            )
            messages.success(request, _('Customer notified of the delay.'))
        return redirect('tasks:task_detail', pk=task.pk)

    if request.method == 'POST' and request.POST.get('action') == 'notify_shipping':
        require_permission(request, RolePermission.Permission.CREATE_TASKS)
        _require_task_owner(request, task)
        if not task.shipping_tracking_number:
            messages.error(request, _('Add a shipping tracking number before notifying the customer.'))
        elif not task.site.effective_contact_email:
            messages.error(request, _('Add a contact email for this site before notifying the customer.'))
        else:
            _send_shipping_notice(
                task.site.effective_contact_name or task.site.customer.name, task.site.name,
                task.task_number, task.shipping_company,
                task.shipping_tracking_number, task.site.effective_contact_email,
            )
            messages.success(request, _('Customer notified of the tracking number.'))
        return redirect('tasks:task_detail', pk=task.pk)

    can_upload_staff_document = requesting_technician.role != Technician.Role.TECHNICIAN
    staff_upload_form = StaffAttachmentUploadForm()
    if request.method == 'POST' and request.POST.get('action') == 'upload_document' and can_upload_staff_document:
        staff_upload_form = StaffAttachmentUploadForm(request.POST, request.FILES)
        if staff_upload_form.is_valid():
            _save_attachment(
                request, task, staff_upload_form.cleaned_data['file'], staff_upload_form.cleaned_data['purpose'],
                source=TaskAttachment.Source.SUPERVISOR,
            )
            messages.success(request, _('Document added.'))
            return redirect('tasks:task_detail', pk=task.pk)

    if request.method == 'POST' and request.POST.get('action') == 'approve_report_supervisor':
        if not can_supervisor_approve(requesting_technician, task):
            raise PermissionDenied
        if approve_report_as_supervisor(task, request.user):
            messages.success(request, _('Approved — now awaiting manager approval before the task closes.'))
        else:
            messages.error(request, _('This task has no report awaiting supervisor review.'))
        return redirect('tasks:task_detail', pk=task.pk)

    if request.method == 'POST' and request.POST.get('action') == 'approve_report':
        require_manager(request)
        if approve_report_as_manager(task, request.user):
            messages.success(request, _('Report approved. Task closed.'))
        else:
            messages.error(request, _('This task has no report awaiting approval.'))
        return redirect('tasks:task_detail', pk=task.pk)

    if request.method == 'POST' and request.POST.get('action') == 'correct_event':
        require_manager(request)
        event = get_object_or_404(
            task.events, pk=request.POST.get('event_id'), event_type__in=CORRECTABLE_EVENTS,
        )
        earliest, latest = _correction_window(event)
        correct_form = CorrectEventTimeForm(request.POST, earliest=earliest, latest=latest)
        if correct_form.is_valid():
            event.corrected_at = correct_form.cleaned_data['corrected_at']
            event.corrected_by = request.user
            event.correction_reason = correct_form.cleaned_data['reason']
            event.save(update_fields=['corrected_at', 'corrected_by', 'correction_reason'])
            messages.success(request, _('Time corrected.'))
        else:
            messages.error(request, ' '.join(error for errors in correct_form.errors.values() for error in errors))
        return redirect('tasks:task_detail', pk=task.pk)

    if request.method == 'POST' and request.POST.get('action') == 'reopen':
        # Admin-only — undoes a manager's close decision, back to
        # "awaiting manager approval" (not further back than that; the
        # filed report itself is untouched — a manager can correct it
        # closed or not, see task_report_correct).
        if requesting_technician.role != Technician.Role.ADMIN:
            raise PermissionDenied
        if task.status != Task.Status.CLOSED:
            messages.error(request, _('This task is not closed.'))
        else:
            task.status = Task.Status.COMPLETED
            task.save(update_fields=['status'])
            TaskEvent.objects.create(
                task=task, event_type=TaskEvent.EventType.REOPENED,
                occurred_at=timezone.now(), actor=request.user,
            )
            messages.success(request, _('Task reopened.'))
        return redirect('tasks:task_detail', pk=task.pk)

    close_task_form = CloseTaskForm()
    if request.method == 'POST' and request.POST.get('action') == 'close_directly':
        require_manager(request)
        if task.status == Task.Status.CLOSED:
            messages.error(request, _('This task is already closed.'))
            return redirect('tasks:task_detail', pk=task.pk)
        close_task_form = CloseTaskForm(request.POST)
        if close_task_form.is_valid():
            task.status = Task.Status.CLOSED
            task.save(update_fields=['status'])
            TaskEvent.objects.create(
                task=task, event_type=TaskEvent.EventType.CLOSED,
                occurred_at=timezone.now(), actor=request.user,
                note=close_task_form.cleaned_data['note'],
            )
            messages.success(request, _('Task closed.'))
            return redirect('tasks:task_detail', pk=task.pk)

    negligence_form = NegligenceFlagForm()
    if request.method == 'POST' and request.POST.get('action') == 'flag_negligence':
        require_manager(request)
        if task.status != Task.Status.CLOSED:
            messages.error(request, _('Only a closed task can be flagged.'))
            return redirect('tasks:task_detail', pk=task.pk)
        negligence_form = NegligenceFlagForm(request.POST)
        if negligence_form.is_valid():
            TaskEvent.objects.create(
                task=task, event_type=TaskEvent.EventType.NEGLIGENCE,
                occurred_at=timezone.now(), actor=request.user,
                note=negligence_form.cleaned_data['note'],
            )
            messages.success(request, _('Task flagged.'))
            return redirect('tasks:task_detail', pk=task.pk)

    if request.method == 'POST' and request.POST.get('action') == 'send_feedback_request':
        require_manager(request)
        feedback = getattr(task, 'feedback', None)
        if task.status != Task.Status.CLOSED:
            messages.error(request, _('Close the task (file its report) before requesting feedback.'))
        elif not task.site.effective_contact_email:
            messages.error(request, _('Add a contact email for this site before requesting feedback.'))
        else:
            if feedback is None:
                feedback = CustomerFeedback.objects.create(
                    task=task, requested_at=timezone.now(), requested_by=request.user,
                )
            else:
                feedback.requested_at = timezone.now()
                feedback.requested_by = request.user
                feedback.save(update_fields=['requested_at', 'requested_by'])
            _send_feedback_email(request, feedback)
            messages.success(request, _('Feedback request sent.'))
        return redirect('tasks:task_detail', pk=task.pk)

    assignments = task.assignments.select_related('technician')
    active_lead = next(
        (a for a in assignments if a.role == TaskAssignment.Role.LEAD and a.is_active), None,
    )
    active_helpers = [a for a in assignments if a.role == TaskAssignment.Role.HELPER and a.is_active]

    events = task.events.select_related('actor', 'corrected_by')
    if not requesting_technician.is_manager_tier:
        # Negligence flags are a manager-tier reliability signal — never
        # shown to the technician being flagged, or to a supervisor
        # viewing the same task.
        events = events.exclude(event_type=TaskEvent.EventType.NEGLIGENCE)

    source_ticket = getattr(task, 'ticket', None)
    pending_schedule_request = task.schedule_change_requests.filter(
        status=ScheduleChangeRequest.Status.PENDING,
    ).select_related('requested_by').first()

    context = {
        'task': task,
        'active_lead': active_lead,
        'active_helpers': active_helpers,
        'events': events,
        'correctable_event_types': CORRECTABLE_EVENTS,
        'attachments': task.attachments.select_related('uploaded_by'),
        'ticket_attachments': source_ticket.attachments.all() if source_ticket else [],
        'task_assets': task.task_assets.select_related('asset'),
        'products': task.products.all(),
        'report': getattr(task, 'report', None),
        'feedback': getattr(task, 'feedback', None),
        'close_task_form': close_task_form,
        'negligence_form': negligence_form,
        'pending_schedule_request': pending_schedule_request,
        'can_upload_staff_document': can_upload_staff_document,
        'staff_upload_form': staff_upload_form,
        'can_approve_as_supervisor': (
            requesting_technician.is_manager_tier
            or requesting_technician.id == task.responsible_supervisor_id
        ),
        'task_messages': task.messages.select_related('sent_by__technician'),
        'message_form': message_form,
    }
    return render(request, 'tasks/task_detail.html', context)


@login_required
def task_edit(request, pk):
    """Edit an existing task's own fields — not its site (a different
    operation) and not its lead/helpers (the assign screen already
    handles that with its own history). Rescheduling logs a
    RESCHEDULED event so there's a record of what the date used to be,
    and — only when a manager has turned auto-notify on — also emails
    the customer the same way the manual "Notify customer" button would.
    """
    requesting_technician = require_permission(request, RolePermission.Permission.CREATE_TASKS)
    active_country = get_active_country(request)
    task = _scoped_or_404(
        Task.objects.select_related('site__customer__country'),
        pk, requesting_technician, active_country, 'site__customer__country',
    )
    _require_task_owner(request, task)
    is_manager = requesting_technician.is_manager_tier
    # The task's own country, not necessarily the manager's active one —
    # a cross-country edit (from the all_tasks board) must still scope
    # responsible_supervisor to who's actually eligible there.
    task_country = task.site.customer.country
    previous_scheduled_for = task.scheduled_for
    previous_scheduled_date = task.scheduled_date
    products_locked = not is_manager and task.status in PRODUCTS_LOCKED_STATUSES

    TaskProductFormSet = formset_factory(TaskProductForm, extra=TASK_PRODUCT_ROWS, can_delete=True)
    product_initial = [
        {
            'product_code': p.product_code, 'serial_number': p.serial_number, 'quantity': p.quantity,
            'replacement': p.replacement, 'frame': p.frame, 'arm': p.arm, 'padding': p.padding,
            'trim': p.trim, 'comment': p.comment, 'note': p.note,
        }
        for p in task.products.all()
    ]

    if request.method == 'POST':
        form = TaskEditForm(
            request.POST, request.FILES, instance=task, country=task_country, is_manager=is_manager,
        )
        product_formset = None if products_locked else TaskProductFormSet(request.POST, prefix='products')
        if form.is_valid() and (products_locked or product_formset.is_valid()):
            with transaction.atomic():
                updated_task = form.save()
                rescheduled = (
                    updated_task.scheduled_for != previous_scheduled_for
                    or updated_task.scheduled_date != previous_scheduled_date
                )
                if rescheduled:
                    TaskEvent.objects.create(
                        task=updated_task, event_type=TaskEvent.EventType.RESCHEDULED,
                        occurred_at=timezone.now(), actor=request.user,
                    )
                if not products_locked:
                    updated_task.products.all().delete()
                    for cleaned in product_formset.cleaned_data:
                        if cleaned.get('product_code') and not cleaned.get('DELETE'):
                            TaskProduct.objects.create(
                                task=updated_task, product_code=cleaned['product_code'],
                                serial_number=cleaned.get('serial_number', ''), quantity=cleaned['quantity'],
                                replacement=cleaned.get('replacement', ''), frame=cleaned.get('frame', ''),
                                arm=cleaned.get('arm', ''), padding=cleaned.get('padding', ''),
                                trim=cleaned.get('trim', ''), comment=cleaned.get('comment', ''),
                                note=cleaned.get('note', ''),
                            )
            notices = []
            if (
                rescheduled and updated_task.scheduled_for and updated_task.site.effective_contact_email
                and NotificationSettings.load().auto_notify_on_reschedule
            ):
                _send_schedule_notification(updated_task)
                updated_task.schedule_notified_at = timezone.now()
                updated_task.schedule_notified_by = request.user
                updated_task.save(update_fields=['schedule_notified_at', 'schedule_notified_by'])
                notices.append(_('Customer notified automatically.'))
            if (
                'shipping_tracking_number' in form.changed_data and updated_task.shipping_tracking_number
                and updated_task.responsible_supervisor_id
            ):
                _send_shipment_notice_to_supervisor(request, updated_task)
                notices.append(_('Supervisor notified of the shipment.'))
            messages.success(request, ' '.join([_('Task updated.')] + notices))
            return redirect('tasks:task_detail', pk=task.pk)
    else:
        form = TaskEditForm(instance=task, country=task_country, is_manager=is_manager)
        product_formset = None if products_locked else TaskProductFormSet(initial=product_initial, prefix='products')

    context = {'task': task, 'form': form, 'product_formset': product_formset, 'products_locked': products_locked}
    return render(request, 'tasks/task_edit.html', context)


@login_required
def task_create(request):
    requesting_technician = require_permission(request, RolePermission.Permission.CREATE_TASKS)
    active_country = get_active_country(request)

    ticket = None
    ticket_id = request.GET.get('ticket')
    if ticket_id:
        ticket = get_object_or_404(
            CustomerTicket, pk=ticket_id, status=CustomerTicket.Status.NEW, country=active_country,
        )

    is_admin = requesting_technician.role == Technician.Role.ADMIN

    if request.method == 'POST':
        form = TaskCreateForm(request.POST, country=active_country, ticket=ticket, is_admin=is_admin)
        if form.is_valid():
            cleaned = form.cleaned_data
            with transaction.atomic():
                site = cleaned.get('site')
                if not site:
                    site = Site.objects.create(
                        customer=cleaned['new_site_customer'], name=cleaned['new_site_name'].strip(),
                        address=cleaned.get('new_site_address', '').strip(),
                        contact_name=cleaned.get('new_site_contact_name', '').strip(),
                        contact_phone=cleaned.get('new_site_contact_phone', '').strip(),
                        access_notes=cleaned.get('new_site_access_notes', '').strip(),
                    )

                brand = cleaned.get('brand')
                if not brand and cleaned.get('new_brand_name'):
                    brand = Brand.objects.create(
                        name=cleaned['new_brand_name'].strip(),
                        portal_url=cleaned.get('new_brand_portal_url', ''),
                    )

                task_type = cleaned.get('task_type')
                if not task_type and cleaned.get('new_task_type_name'):
                    task_type = TaskType.objects.create(
                        code=cleaned['new_task_type_code'].strip(), name=cleaned['new_task_type_name'].strip(),
                        name_ar=cleaned['new_task_type_name_ar'].strip(),
                        category=cleaned['new_task_type_category'],
                    )

                task = form.save(commit=False)
                task.site = site
                task.brand = brand
                task.task_type = task_type
                task.created_by = request.user
                task.status = Task.Status.NEW
                if ticket is not None:
                    task.pak_reference_number = ticket.pak_reference_number
                    task.shipping_company = ticket.shipping_company
                    task.shipping_tracking_number = ticket.shipping_tracking_number
                    task.reported_serial_numbers = ticket.serial_numbers
                # A manager's own schedule is locked from the moment it's
                # set — a supervisor's stays open. See task_edit for the
                # same rule applied to a reschedule.
                task.schedule_time_locked = requesting_technician.is_manager_tier and bool(task.scheduled_for)
                _save_new_task(task)
                TaskEvent.objects.create(
                    task=task, event_type=TaskEvent.EventType.CREATED,
                    occurred_at=timezone.now(), actor=request.user,
                )
                TaskNotification.objects.create(task=task, created_at=timezone.now())
                if ticket is not None:
                    ticket.task = task
                    ticket.customer = site.customer
                    ticket.status = CustomerTicket.Status.CONVERTED
                    ticket.reviewed_by = request.user
                    ticket.reviewed_at = timezone.now()
                    ticket.save(update_fields=['task', 'customer', 'status', 'reviewed_by', 'reviewed_at'])
            _send_new_task_email_to_staff(request, task)
            messages.success(request, _('Task %(number)s created.') % {'number': task.task_number})
            return redirect('tasks:task_detail', pk=task.pk)
    else:
        form = TaskCreateForm(country=active_country, ticket=ticket, is_admin=is_admin)

    # So whoever's creating this can see everyone's upcoming schedule
    # right here and pick a date that doesn't collide — no separate trip
    # to the week board just to check.
    team_schedule = _supervisors_with_next_scheduled_task(
        Technician.objects.filter(is_active=True, country=active_country).exclude(role=Technician.Role.TECHNICIAN),
    ) + _technicians_with_next_scheduled_task(
        Technician.objects.filter(is_active=True, country=active_country, role=Technician.Role.TECHNICIAN),
    )

    return render(request, 'tasks/task_create.html', {'form': form, 'ticket': ticket, 'team_schedule': team_schedule})


def ticket_form(request):
    """Retired — a ticket now always requires a customer login. A brand
    new customer reaches the company outside the app (phone, WhatsApp,
    email); staff registers them, and every ticket after that goes
    through the portal (customers.views.portal_ticket_new). This URL is
    kept only so an old bookmark or shared link lands somewhere useful
    instead of a dead page.
    """
    return redirect('customers:portal_login')


def _ticket_is_open(ticket):
    """Open for replies while it's still new, or converted but the
    resulting task isn't finished yet — closed once dismissed, or once
    that task is actually closed. Not tied to the ticket's own `status`
    alone: "converted" can mean the work just started. A cancelled task
    does NOT close it — cancellation isn't a resolution, the customer's
    issue is still unresolved, so the conversation has to stay open.
    """
    if ticket.status in (CustomerTicket.Status.DISMISSED, CustomerTicket.Status.CLOSED):
        return False
    if ticket.status == CustomerTicket.Status.CONVERTED:
        return ticket.task is not None and ticket.task.status != Task.Status.CLOSED
    return True


def send_ticket_confirmation_to_customer(request, ticket):
    """The one email a customer gets right when they submit a ticket —
    best-effort and forced to English, same reasoning as every other
    outbound customer notification here. Public (no leading underscore):
    customers.views.portal_ticket_new, a different app, calls this
    directly, the same way it already calls save_new_ticket.
    """
    if not ticket.contact_email:
        return
    link = request.build_absolute_uri(reverse('tasks:ticket_status', args=[ticket.token]))
    with translation.override('en'):
        subject = _('Ticket %(number)s received — Profit Sports Solutions') % {'number': ticket.ticket_number}
        message = _(
            'Hi %(contact)s,\n\n'
            "Thanks for contacting us. We've received your request and created ticket #%(number)s.\n\n"
            "Our team responds within 24 hours, Sunday–Friday. You'll get an email as soon as there's an update.\n\n"
            "Please make sure you've attached photos or videos for each issue you're facing. This helps our "
            'team understand the problem and resolve your case faster.\n\n'
            'Best regards,\n'
            'Profit Sports Solutions\n',
        ) % {'contact': ticket.contact_name, 'number': ticket.ticket_number}
    _send_notification_email(
        subject, message, 'tasks/email/ticket_confirmation.html',
        {'contact': ticket.contact_name, 'ticket_number': ticket.ticket_number, 'link': link},
        [ticket.contact_email],
    )


def send_customer_login_email(request, customer, password):
    """The one email that actually delivers a new portal login — the
    temporary password plus a link to sign in. Public: customers.views
    calls this right after creating the login, a different app, same
    reasoning as send_ticket_confirmation_to_customer. Best-effort and
    forced to English, same as every other outbound customer email here;
    if it fails to send, the password is still shown once on screen to
    whoever created the login, so nothing is lost.
    """
    link = request.build_absolute_uri(reverse('customers:portal_login'))
    contact = customer.contact_name or customer.name
    with translation.override('en'):
        subject = _('Your Profit Sports Solutions portal login')
        message = _(
            'Hi %(contact)s,\n\n'
            "We've created your customer portal account — from here you can submit service "
            'requests and track their progress.\n\n'
            'Username: %(username)s\n'
            'Temporary password: %(password)s\n\n'
            "You'll be asked to set your own password and confirm your details the first time "
            'you sign in.\n\n'
            'Sign in here:\n%(link)s\n\n'
            'Best regards,\n'
            'Profit Sports Solutions\n',
        ) % {'contact': contact, 'username': customer.user.username, 'password': password, 'link': link}
    _send_notification_email(
        subject, message, 'tasks/email/customer_login_credentials.html',
        {'contact': contact, 'username': customer.user.username, 'password': password, 'link': link},
        [customer.user.email],
    )


def send_technician_login_email(request, technician, password):
    """The one email that actually delivers a new staff login — same
    shape as send_customer_login_email, just the staff-side login page.
    Best-effort; if it fails, the password is still shown once on screen
    to whoever created the login.
    """
    link = request.build_absolute_uri(reverse('login'))
    with translation.override('en'):
        subject = _('Your Profit Sports Solutions account')
        message = _(
            'Hi %(contact)s,\n\n'
            'Your Profit Sports Solutions account has been created.\n\n'
            'Username: %(username)s\n'
            'Temporary password: %(password)s\n\n'
            "You'll be asked to set your own password the first time you sign in.\n\n"
            'Sign in here:\n%(link)s\n\n'
            'Best regards,\n'
            'Profit Sports Solutions\n',
        ) % {'contact': technician.full_name, 'username': technician.user.username, 'password': password, 'link': link}
    _send_notification_email(
        subject, message, 'tasks/email/technician_login_credentials.html',
        {'contact': technician.full_name, 'username': technician.user.username, 'password': password, 'link': link},
        [technician.user.email],
    )


def _send_reply_to_customer(request, reply):
    """Best-effort, and only when the customer gave an email — same
    fail-silent pattern as every other customer-facing notification.
    Any file attached to the reply (quotation, report, photo, whatever
    staff chose to include) rides along on the email itself, not just
    linked from the portal.
    """
    ticket = reply.ticket
    if not ticket.contact_email:
        return
    link = request.build_absolute_uri(reverse('tasks:ticket_status', args=[ticket.token]))
    with translation.override('en'):
        subject = _('New reply on your ticket %(number)s — %(site)s') % {
            'number': ticket.ticket_number, 'site': ticket.site_description,
        }
        message = _(
            'Dear %(contact)s,\n\n'
            'Regarding ticket %(number)s:\n\n'
            '%(reply_message)s\n\n'
            'You can reply or check the full conversation here:\n%(link)s\n\n'
            'Best regards,\n'
            'Profit Sports Solutions\n',
        ) % {
            'contact': ticket.contact_name, 'number': ticket.ticket_number,
            'reply_message': reply.message, 'link': link,
        }
    _send_notification_email(
        subject, message, 'tasks/email/reply_to_customer.html',
        {
            'contact': ticket.contact_name, 'ticket_number': ticket.ticket_number,
            'reply_message': reply.message, 'link': link,
        },
        [ticket.contact_email], attachment=reply.attachment,
    )


def _send_reply_to_staff(request, reply):
    """Best-effort, and only when the ticket is assigned to someone with
    a login and an email on file — there's no fixed office address to
    fall back to.
    """
    ticket = reply.ticket
    assignee = ticket.assigned_to
    if not assignee or not assignee.user_id or not assignee.user.email:
        return
    link = request.build_absolute_uri(reverse('tasks:ticket_review', args=[ticket.pk]))
    with translation.override('en'):
        subject = _('New customer reply — ticket %(number)s') % {'number': ticket.ticket_number}
        message = _(
            'The customer replied on ticket %(number)s (%(company)s — %(site)s):\n\n'
            '%(reply_message)s\n\n'
            'View and reply here:\n%(link)s\n',
        ) % {
            'number': ticket.ticket_number, 'company': ticket.company_name, 'site': ticket.site_description,
            'reply_message': reply.message, 'link': link,
        }
    _send_notification_email(
        subject, message, 'tasks/email/reply_to_staff.html',
        {
            'ticket_number': ticket.ticket_number, 'company': ticket.company_name,
            'site': ticket.site_description, 'reply_message': reply.message, 'link': link,
        },
        [assignee.user.email],
    )


def _staff_notification_recipients(country, roles):
    """Logins in this country, among these roles, with an email on file —
    who gets alerted about a new ticket or a new task. The bell only
    reaches someone already in the app; this reaches them either way.
    """
    return list(
        Technician.objects.filter(
            is_active=True, country=country, role__in=roles, user__isnull=False,
        ).exclude(user__email='').values_list('user__email', flat=True)
    )


def _ticket_manager_roles():
    """Whichever role(s) RolePermission.MANAGE_TICKETS currently allows —
    the same set AssignTicketForm offers, so a new ticket's email goes to
    whoever can actually act on it, not a hardcoded Manager/Admin pair.
    """
    return list(
        RolePermission.objects.filter(
            permission=RolePermission.Permission.MANAGE_TICKETS, allowed=True,
        ).values_list('role', flat=True),
    )


def send_new_ticket_email_to_staff(request, ticket):
    """Best-effort, same fail-silent pattern as every other notification
    here — everyone whose role can manage_tickets, in the ticket's
    country, alongside the bell (people.context_processors.notification_
    bell). Public (no leading underscore): customers.views.portal_ticket_
    new calls this directly, the same way it already calls save_new_ticket.
    """
    recipients = _staff_notification_recipients(ticket.country, _ticket_manager_roles())
    if not recipients:
        return
    link = request.build_absolute_uri(reverse('tasks:ticket_review', args=[ticket.pk]))
    with translation.override('en'):
        subject = _('New ticket %(number)s — %(company)s') % {
            'number': ticket.ticket_number, 'company': ticket.company_name,
        }
        message = _(
            'A new ticket has come in.\n\n'
            'Ticket: %(number)s\n'
            'Company: %(company)s\n'
            'Site: %(site)s\n'
            'Description: %(description)s\n\n'
            'Review it here:\n%(link)s\n',
        ) % {
            'number': ticket.ticket_number, 'company': ticket.company_name,
            'site': ticket.site_description, 'description': ticket.description, 'link': link,
        }
    _send_notification_email(
        subject, message, 'tasks/email/new_ticket_to_staff.html',
        {
            'ticket_number': ticket.ticket_number, 'company': ticket.company_name,
            'site': ticket.site_description, 'description': ticket.description, 'link': link,
        },
        recipients,
    )


def _send_new_task_email_to_staff(request, task):
    """Best-effort, same fail-silent pattern as every other notification
    here — every manager/admin in the task's country, alongside the bell.
    """
    recipients = _staff_notification_recipients(
        task.site.customer.country, [Technician.Role.MANAGER, Technician.Role.ADMIN],
    )
    if not recipients:
        return
    link = request.build_absolute_uri(reverse('tasks:task_detail', args=[task.pk]))
    with translation.override('en'):
        subject = _('New task %(number)s — %(site)s') % {'number': task.task_number, 'site': task.site.name}
        message = _(
            'A new task was created.\n\n'
            'Task: %(number)s\n'
            'Site: %(site)s\n'
            'Description: %(description)s\n\n'
            'View it here:\n%(link)s\n',
        ) % {'number': task.task_number, 'site': task.site.name, 'description': task.description, 'link': link}
    _send_notification_email(
        subject, message, 'tasks/email/new_task_to_staff.html',
        {'task_number': task.task_number, 'site': task.site.name, 'description': task.description, 'link': link},
        recipients,
    )


@login_required(login_url='customers:portal_login')
def ticket_status(request, token):
    """A logged-in customer checking on a ticket they submitted, and
    replying while it's still open. The token in the URL still picks
    the ticket, but it's no longer the customer's identity — every
    ticket now belongs to a logged-in customer's account (tickets can
    only be submitted while signed in), so this only renders it for
    that same customer, never anyone else who happens to have the link.
    """
    ticket = get_object_or_404(CustomerTicket, token=token)
    customer = getattr(request.user, 'customer', None)
    if customer is None or ticket.customer_id != customer.id:
        raise PermissionDenied
    can_reply = _ticket_is_open(ticket)
    reply_form = TicketReplyForm()

    if request.method == 'POST' and can_reply:
        reply_form = TicketReplyForm(request.POST, request.FILES)
        if reply_form.is_valid():
            reply = TicketReply.objects.create(
                ticket=ticket, sender=TicketReply.Sender.CUSTOMER, sent_by=request.user,
                message=reply_form.cleaned_data['message'],
                attachment=reply_form.cleaned_data['attachment'], sent_at=timezone.now(),
            )
            TicketNotification.objects.create(
                ticket=ticket, kind=TicketNotification.Kind.NEW_REPLY, created_at=timezone.now(),
            )
            _send_reply_to_staff(request, reply)
            messages.success(request, _('Reply sent.'))
            return redirect('tasks:ticket_status', token=token)

    context = {
        'ticket': ticket, 'can_reply': can_reply, 'reply_form': reply_form,
        'replies': ticket.replies.select_related('sent_by'),
    }
    return render(request, 'tasks/ticket_status.html', context)


@login_required
def ticket_list(request):
    """Every customer-submitted ticket still needing a decision, plus
    what's already been resolved — country-scoped like everything else a
    supervisor reviews. A separate, independent field for each thing
    you'd actually search a ticket by, instead of one combined box.
    """
    require_permission(request, RolePermission.Permission.MANAGE_TICKETS)

    status = request.GET.get('status', 'new')
    ticket_id = request.GET.get('ticket_id', '').strip()
    company = request.GET.get('company', '').strip()
    site = request.GET.get('site', '').strip()
    pak = request.GET.get('pak', '').strip()
    customer_id = request.GET.get('customer', '')
    date = parse_date(request.GET.get('date', '') or '')
    active_country = get_active_country(request)

    tickets = CustomerTicket.objects.filter(country=active_country).select_related('assigned_to', 'customer')
    if status != 'all':
        tickets = tickets.filter(status=status)
    if ticket_id:
        tickets = tickets.filter(ticket_number__icontains=ticket_id)
    if company:
        tickets = tickets.filter(company_name__icontains=company)
    if site:
        tickets = tickets.filter(Q(site_description__icontains=site) | Q(site_address__icontains=site))
    if pak:
        tickets = tickets.filter(pak_reference_number__icontains=pak)
    if customer_id:
        # Matches the linked customer where one's been set (portal
        # submission or an earlier conversion) — and, since a brand new
        # ticket has no link yet, also whatever name the customer typed
        # that matches one you already have, same case-insensitive match
        # task_create uses to find it.
        customer = get_object_or_404(Customer, pk=customer_id, country=active_country)
        tickets = tickets.filter(Q(customer_id=customer_id) | Q(company_name__iexact=customer.name))
    if date:
        tickets = tickets.filter(submitted_at__date=date)
    tickets = tickets.order_by('-submitted_at')

    filter_params = {
        key: value for key, value in {
            'ticket_id': ticket_id, 'company': company, 'site': site, 'pak': pak, 'customer': customer_id,
            'date': request.GET.get('date', ''),
        }.items() if value
    }

    context = {
        'tickets': tickets, 'status': status, 'status_choices': CustomerTicket.Status.choices,
        'ticket_id': ticket_id, 'company': company, 'site': site, 'pak': pak,
        'date': request.GET.get('date', ''),
        'customers': Customer.objects.filter(is_active=True, country=active_country).order_by('name'),
        'selected_customer': customer_id,
        'filter_qs': urlencode(filter_params),
    }
    return render(request, 'tasks/ticket_list.html', context)


@login_required
def all_tickets(request):
    """Every customer-submitted ticket in every country — gated on
    manage_tickets like the rest of ticket triage, not the is_manager_tier
    fixed floor: tickets are their own, narrower permission now, not
    something every manager gets for free. A separate, independent field
    for each thing you'd actually search a ticket by, same as all_tasks.
    Converting one into a task still requires switching to its own
    country first (task_create's site/brand/technician choices are all
    built around the active country); dismissing or assigning it works
    from here regardless.
    """
    require_permission(request, RolePermission.Permission.MANAGE_TICKETS)

    status = request.GET.get('status', 'new')
    ticket_id = request.GET.get('ticket_id', '').strip()
    company = request.GET.get('company', '').strip()
    site = request.GET.get('site', '').strip()
    pak = request.GET.get('pak', '').strip()
    customer_id = request.GET.get('customer', '')
    date = parse_date(request.GET.get('date', '') or '')
    country_id = request.GET.get('country', '')

    tickets = CustomerTicket.objects.select_related('assigned_to', 'country', 'customer')
    if status != 'all':
        tickets = tickets.filter(status=status)
    if ticket_id:
        tickets = tickets.filter(ticket_number__icontains=ticket_id)
    if company:
        tickets = tickets.filter(company_name__icontains=company)
    if site:
        tickets = tickets.filter(Q(site_description__icontains=site) | Q(site_address__icontains=site))
    if pak:
        tickets = tickets.filter(pak_reference_number__icontains=pak)
    if customer_id:
        # Same case-insensitive name match task_create uses, so a ticket
        # not yet linked to a customer (nothing converted or logged in)
        # still shows up under the one it's actually about.
        customer = get_object_or_404(Customer, pk=customer_id)
        tickets = tickets.filter(Q(customer_id=customer_id) | Q(company_name__iexact=customer.name))
    if date:
        tickets = tickets.filter(submitted_at__date=date)
    if country_id:
        tickets = tickets.filter(country_id=country_id)
    tickets = tickets.order_by('-submitted_at')

    filter_params = {
        key: value for key, value in {
            'ticket_id': ticket_id, 'company': company, 'site': site, 'pak': pak, 'customer': customer_id,
            'date': request.GET.get('date', ''), 'country': country_id,
        }.items() if value
    }

    context = {
        'tickets': tickets, 'status': status, 'status_choices': CustomerTicket.Status.choices,
        'ticket_id': ticket_id, 'company': company, 'site': site, 'pak': pak,
        'date': request.GET.get('date', ''),
        'countries': Country.objects.order_by('name'), 'selected_country': country_id,
        'customers': Customer.objects.filter(is_active=True).order_by('name'),
        'selected_customer': customer_id,
        'filter_qs': urlencode(filter_params),
    }
    return render(request, 'tasks/all_tickets.html', context)


@login_required
def ticket_review(request, pk):
    """Dismiss, assign, or convert a ticket — restricted to manage_tickets
    like the rest of ticket triage. Whoever it's currently assigned to can
    also open it read-only, even without that permission, so they can see
    what they've been asked to look into; they can't act on it themselves.
    """
    technician = require_technician(request)
    # Country-scoped in the fetch itself, same as every other cross-country
    # lookup in this app (technician_board, technician_skills, ...) — an
    # assignee is always same-country by construction (AssignTicketForm
    # only offers same-country technicians), so this costs it nothing. A
    # manager reaches any ticket regardless, same as _scoped_or_404 gives
    # them everywhere else — the point of the all_tickets board.
    ticket = _scoped_or_404(CustomerTicket.objects, pk, technician, get_active_country(request), 'country')

    can_manage = RolePermission.objects.filter(
        role=technician.role, permission=RolePermission.Permission.MANAGE_TICKETS, allowed=True,
    ).exists()
    is_assignee = ticket.assigned_to_id == technician.id
    if not (can_manage or is_assignee):
        raise PermissionDenied

    if can_manage:
        ticket.notifications.filter(seen_at__isnull=True).update(seen_at=timezone.now())

    dismiss_form = DismissTicketForm()
    close_form = CloseTicketForm()
    assign_form = AssignTicketForm(country=ticket.country, initial={'assigned_to': ticket.assigned_to_id})
    logistics_form = TicketLogisticsForm(instance=ticket)
    edit_form = TicketEditForm(instance=ticket)
    reply_form = TicketReplyForm()
    can_reply = _ticket_is_open(ticket)
    internal_note_form = TicketInternalNoteForm()

    if request.method == 'POST' and can_manage:
        action = request.POST.get('action')

        if action == 'add_internal_note' and can_manage:
            internal_note_form = TicketInternalNoteForm(request.POST, request.FILES)
            if internal_note_form.is_valid():
                TicketInternalNote.objects.create(
                    ticket=ticket, author=request.user,
                    message=internal_note_form.cleaned_data['message'],
                    attachment=internal_note_form.cleaned_data['attachment'], created_at=timezone.now(),
                )
                messages.success(request, _('Note added.'))
                return redirect('tasks:ticket_review', pk=ticket.pk)

        elif action == 'add_reply' and can_reply:
            reply_form = TicketReplyForm(request.POST, request.FILES)
            if reply_form.is_valid():
                reply = TicketReply.objects.create(
                    ticket=ticket, sender=TicketReply.Sender.STAFF, sent_by=request.user,
                    message=reply_form.cleaned_data['message'],
                    attachment=reply_form.cleaned_data['attachment'],
                    sent_at=timezone.now(),
                )
                _send_reply_to_customer(request, reply)
                messages.success(request, _('Reply sent.'))
                return redirect('tasks:ticket_review', pk=ticket.pk)

        elif action == 'update_logistics':
            logistics_form = TicketLogisticsForm(request.POST, instance=ticket)
            if logistics_form.is_valid():
                logistics_form.save()
                messages.success(request, _('Logistics updated.'))
                return redirect('tasks:ticket_review', pk=ticket.pk)

        elif action == 'edit_details':
            edit_form = TicketEditForm(request.POST, instance=ticket)
            if edit_form.is_valid():
                edit_form.save()
                messages.success(request, _('Ticket details updated.'))
                return redirect('tasks:ticket_review', pk=ticket.pk)

        elif action == 'notify_shipping':
            if not ticket.shipping_tracking_number:
                messages.error(request, _('Add a shipping tracking number before notifying the customer.'))
            elif not ticket.contact_email:
                messages.error(request, _('Add a contact email before notifying the customer.'))
            else:
                _send_shipping_notice(
                    ticket.contact_name, ticket.site_description, f'ticket #{ticket.pk}',
                    ticket.shipping_company,
                    ticket.shipping_tracking_number, ticket.contact_email,
                )
                messages.success(request, _('Customer notified of the tracking number.'))
            return redirect('tasks:ticket_review', pk=ticket.pk)

        elif action == 'dismiss' and ticket.status == CustomerTicket.Status.NEW:
            dismiss_form = DismissTicketForm(request.POST)
            if dismiss_form.is_valid():
                ticket.status = CustomerTicket.Status.DISMISSED
                ticket.dismissal_reason = dismiss_form.cleaned_data['dismissal_reason']
                ticket.reviewed_by = request.user
                ticket.reviewed_at = timezone.now()
                ticket.save(update_fields=['status', 'dismissal_reason', 'reviewed_by', 'reviewed_at'])
                messages.success(request, _('Ticket dismissed.'))
                return redirect('tasks:ticket_review', pk=ticket.pk)

        elif action == 'close' and ticket.status != CustomerTicket.Status.CLOSED:
            # Any status but already-closed — support_manager/admin can
            # close a ticket at any point in its life, not just while it's
            # still new; a closed one goes through 'reopen' instead.
            close_form = CloseTicketForm(request.POST)
            if close_form.is_valid():
                ticket.status = CustomerTicket.Status.CLOSED
                ticket.close_reason = close_form.cleaned_data['close_reason']
                ticket.reviewed_by = request.user
                ticket.reviewed_at = timezone.now()
                ticket.save(update_fields=['status', 'close_reason', 'reviewed_by', 'reviewed_at'])
                messages.success(request, _('Ticket closed.'))
                return redirect('tasks:ticket_review', pk=ticket.pk)

        elif action == 'reopen' and technician.role == Technician.Role.ADMIN:
            # Admin-only — support_manager can close, but reopening a
            # decision someone already signed off on is one step further,
            # same fixed-floor reasoning as require_admin elsewhere.
            if ticket.status != CustomerTicket.Status.CLOSED:
                messages.error(request, _('This ticket is not closed.'))
            else:
                ticket.status = CustomerTicket.Status.NEW
                ticket.save(update_fields=['status'])
                messages.success(request, _('Ticket reopened.'))
            return redirect('tasks:ticket_review', pk=ticket.pk)

        elif action == 'assign':
            assign_form = AssignTicketForm(request.POST, country=ticket.country)
            if assign_form.is_valid():
                ticket.assigned_to = assign_form.cleaned_data['assigned_to']
                ticket.assigned_at = timezone.now()
                ticket.save(update_fields=['assigned_to', 'assigned_at'])
                messages.success(request, _('Ticket assigned.'))
                return redirect('tasks:ticket_review', pk=ticket.pk)

    context = {
        'ticket': ticket, 'dismiss_form': dismiss_form, 'close_form': close_form, 'assign_form': assign_form,
        'logistics_form': logistics_form, 'edit_form': edit_form, 'can_manage': can_manage,
        'reply_form': reply_form, 'can_reply': can_reply, 'is_admin': technician.role == Technician.Role.ADMIN,
        'replies': ticket.replies.select_related('sent_by'),
    }
    if can_manage:
        context['internal_note_form'] = internal_note_form
        context['internal_notes'] = ticket.internal_notes.select_related('author')
    return render(request, 'tasks/ticket_review.html', context)


def _require_task_owner(request, task):
    """Once a task has a responsible supervisor, only they (or a manager)
    can edit it, manage its assignment, or act on it from its detail page.
    An unowned task (no responsible supervisor set yet) stays open to
    anyone the usual permission already let in — same as before this
    field existed, and how it gets claimed in the first place.
    """
    technician = request.user.technician
    if technician.is_manager_tier:
        return
    if task.responsible_supervisor_id and task.responsible_supervisor_id != technician.id:
        raise PermissionDenied


def _set_lead(task, active_lead, technician, end_reason, actor):
    with transaction.atomic():
        if active_lead:
            active_lead.is_active = False
            active_lead.ended_at = timezone.now()
            active_lead.end_reason = end_reason
            active_lead.save()
            event_type = TaskEvent.EventType.REASSIGNED
        else:
            event_type = TaskEvent.EventType.ASSIGNED

        TaskAssignment.objects.create(
            task=task, technician=technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        TaskEvent.objects.create(task=task, event_type=event_type, occurred_at=timezone.now(), actor=actor)

        if task.status in STATUSES_RESET_BY_ASSIGNMENT:
            task.status = Task.Status.ASSIGNED
            task.save(update_fields=['status'])


@login_required
def task_assign(request, pk):
    requesting_technician = require_permission(request, RolePermission.Permission.ASSIGN_TASKS)

    task = _scoped_or_404(
        Task.objects.select_related('site__customer__country'),
        pk, requesting_technician, get_active_country(request), 'site__customer__country',
    )
    _require_task_owner(request, task)
    active_assignments = list(task.assignments.filter(is_active=True).select_related('technician'))
    active_lead = next((a for a in active_assignments if a.role == TaskAssignment.Role.LEAD), None)
    active_helpers = [a for a in active_assignments if a.role == TaskAssignment.Role.HELPER]
    assigned_ids = {a.technician_id for a in active_assignments}

    locked = task.status in ASSIGNMENT_LOCKED_STATUSES
    candidates_qs = _assignment_candidates(task, exclude_ids=assigned_ids)
    # Shown in the candidates table regardless of availability, so a
    # supervisor can see and flip someone back — but only available
    # technicians can actually be picked as lead or helper.
    selectable_qs = candidates_qs.filter(is_available=True)

    set_lead_form = SetLeadForm(technicians=selectable_qs, requires_reason=bool(active_lead))
    add_helper_form = AddHelperForm(technicians=selectable_qs) if active_lead else None

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'set_lead' and not locked:
            set_lead_form = SetLeadForm(request.POST, technicians=selectable_qs, requires_reason=bool(active_lead))
            if set_lead_form.is_valid():
                _set_lead(
                    task, active_lead, set_lead_form.cleaned_data['technician'],
                    set_lead_form.cleaned_data.get('end_reason', ''), request.user,
                )
                messages.success(request, _('Lead technician set.'))
                return redirect('tasks:task_assign', pk=task.pk)

        elif action == 'add_helper' and active_lead and not locked:
            add_helper_form = AddHelperForm(request.POST, technicians=selectable_qs)
            if add_helper_form.is_valid():
                TaskAssignment.objects.create(
                    task=task, technician=add_helper_form.cleaned_data['technician'],
                    role=TaskAssignment.Role.HELPER, assigned_at=timezone.now(), is_active=True,
                )
                messages.success(request, _('Helper added.'))
                return redirect('tasks:task_assign', pk=task.pk)

        elif action == 'remove_helper' and not locked:
            helper = get_object_or_404(
                TaskAssignment, pk=request.POST.get('assignment_id'), task=task,
                role=TaskAssignment.Role.HELPER, is_active=True,
            )
            remove_form = RemoveAssignmentForm(request.POST)
            if remove_form.is_valid():
                helper.is_active = False
                helper.ended_at = timezone.now()
                helper.end_reason = remove_form.cleaned_data['end_reason']
                helper.save()
                messages.success(request, _('Helper removed.'))
                return redirect('tasks:task_assign', pk=task.pk)

    context = {
        'task': task,
        'active_lead': active_lead,
        'active_helpers': active_helpers,
        'candidates': _technicians_with_next_scheduled_task(_candidates_with_skill_level(candidates_qs, task)),
        'locked': locked,
        'set_lead_form': set_lead_form,
        'add_helper_form': add_helper_form,
        'remove_form': RemoveAssignmentForm(),
    }
    return render(request, 'tasks/task_assign.html', context)


@login_required
def technician_availability(request):
    """Marking who's available for work — carved out of task_assign
    entirely: a technician's availability isn't specific to any one
    task (the same person shows up, available or not, on every other
    task's candidate list), so controlling it from inside one task's
    assignment screen was misleading — flip it there and it changes for
    every task, not just the one on screen. This is the one place it's
    controlled from now; task_assign only ever displays the status.
    """
    require_permission(request, RolePermission.Permission.ASSIGN_TASKS)
    active_country = get_active_country(request)
    technicians = Technician.objects.filter(is_active=True, country=active_country).order_by('full_name')
    mark_unavailable_form = MarkUnavailableForm()

    if request.method == 'POST':
        action = request.POST.get('action')
        technician = get_object_or_404(
            Technician, pk=request.POST.get('technician_id'), country=active_country,
        )

        if action == 'mark_unavailable':
            mark_unavailable_form = MarkUnavailableForm(request.POST)
            if mark_unavailable_form.is_valid():
                technician.is_available = False
                technician.unavailable_reason = mark_unavailable_form.cleaned_data['reason']
                technician.save(update_fields=['is_available', 'unavailable_reason'])
                messages.success(request, _('Marked unavailable.'))
                return redirect('tasks:technician_availability')

        elif action == 'mark_available':
            technician.is_available = True
            technician.unavailable_reason = ''
            technician.save(update_fields=['is_available', 'unavailable_reason'])
            messages.success(request, _('Marked available.'))
            return redirect('tasks:technician_availability')

    return render(request, 'tasks/technician_availability.html', {
        'technicians': technicians, 'mark_unavailable_form': mark_unavailable_form,
    })


@login_required
def task_week(request):
    require_permission(request, RolePermission.Permission.VIEW_TASKS)
    active_country = get_active_country(request)

    today, start, end = _week_window(request)

    scheduled = _with_lead_prefetch(
        Task.objects.filter(
            scheduled_for__date__range=(start, end), site__customer__country=active_country,
        )
        .select_related('site__customer', 'task_type').order_by('scheduled_for'),
    )
    _attach_lead_technician(scheduled)

    days = [{'date': start + timedelta(days=offset), 'tasks': []} for offset in range(WEEK_LENGTH)]
    tasks_by_date = {day['date']: day['tasks'] for day in days}
    for task in scheduled:
        tasks_by_date[timezone.localtime(task.scheduled_for).date()].append(task)

    unscheduled = _with_lead_prefetch(
        Task.objects.filter(
            scheduled_for__isnull=True, status__in=OPEN_STATUSES, site__customer__country=active_country,
        )
        .select_related('site__customer', 'task_type')
        .annotate(priority_rank=PRIORITY_RANK).order_by('priority_rank', 'promised_at'),
    )
    _attach_lead_technician(unscheduled)

    context = {
        'days': days,
        'unscheduled': unscheduled,
        **_week_nav_context(today, start),
    }
    return render(request, 'tasks/task_week.html', context)


@login_required
def all_week(request):
    """Every country's week board at once — the same day/unscheduled
    shape as task_week, minus the country boundary, with a country label
    per task since a day's list can now mix countries. Manager-only,
    same fixed floor as the other "all ..." boards.
    """
    require_manager(request)

    today, start, end = _week_window(request)

    scheduled = _with_lead_prefetch(
        Task.objects.filter(scheduled_for__date__range=(start, end))
        .select_related('site__customer__country', 'task_type').order_by('scheduled_for'),
    )
    _attach_lead_technician(scheduled)

    days = [{'date': start + timedelta(days=offset), 'tasks': []} for offset in range(WEEK_LENGTH)]
    tasks_by_date = {day['date']: day['tasks'] for day in days}
    for task in scheduled:
        tasks_by_date[timezone.localtime(task.scheduled_for).date()].append(task)

    unscheduled = _with_lead_prefetch(
        Task.objects.filter(scheduled_for__isnull=True, status__in=OPEN_STATUSES)
        .select_related('site__customer__country', 'task_type')
        .annotate(priority_rank=PRIORITY_RANK).order_by('priority_rank', 'promised_at'),
    )
    _attach_lead_technician(unscheduled)

    context = {
        'days': days,
        'unscheduled': unscheduled,
        **_week_nav_context(today, start),
    }
    return render(request, 'tasks/all_week.html', context)


def _week_board(technician, request):
    """(days, unscheduled, nav_context) for one technician's week — the
    start time and estimated finish (Task.estimated_finish) travel with
    each task automatically, since that's a model property. Shared by
    my_week (self) and technician_board (a supervisor viewing someone else).

    Covers both ways a task can belong to someone: assigned as lead/helper
    (TaskAssignment — how a technician gets work), and set as
    responsible_supervisor (how a supervisor/manager is on the hook for
    one) — a task where both are true only appears once, under whichever
    role. Without the second half, this board was blank for anyone whose
    only stake in a task was being its responsible_supervisor, which is
    exactly the case someone scheduling a new task most needs to see.
    """
    today, start, end = _week_window(request)

    scheduled_assignments = TaskAssignment.objects.filter(
        technician=technician, is_active=True, task__scheduled_for__date__range=(start, end),
    ).select_related('task__site__customer', 'task__task_type').order_by('task__scheduled_for')

    days = [{'date': start + timedelta(days=offset), 'tasks': []} for offset in range(WEEK_LENGTH)]
    tasks_by_date = {day['date']: day['tasks'] for day in days}
    assigned_task_ids = set()
    for assignment in scheduled_assignments:
        task = assignment.task
        task.my_role = assignment.role
        tasks_by_date[timezone.localtime(task.scheduled_for).date()].append(task)
        assigned_task_ids.add(task.pk)

    responsible_scheduled = Task.objects.filter(
        responsible_supervisor=technician, scheduled_for__date__range=(start, end),
    ).exclude(pk__in=assigned_task_ids).select_related('site__customer', 'task_type')
    for task in responsible_scheduled:
        task.my_role = 'responsible'
        tasks_by_date[timezone.localtime(task.scheduled_for).date()].append(task)

    for day in days:
        day['tasks'].sort(key=lambda t: t.scheduled_for)

    unscheduled_assignments = TaskAssignment.objects.filter(
        technician=technician, is_active=True, task__scheduled_for__isnull=True,
        task__status__in=OPEN_STATUSES,
    )
    role_by_task_id = {a.task_id: a.role for a in unscheduled_assignments}
    unscheduled = list(Task.objects.filter(pk__in=role_by_task_id).select_related(
        'site__customer', 'task_type',
    ).annotate(priority_rank=PRIORITY_RANK).order_by('priority_rank', 'promised_at'))
    for task in unscheduled:
        task.my_role = role_by_task_id[task.pk]

    unscheduled_responsible = list(Task.objects.filter(
        responsible_supervisor=technician, scheduled_for__isnull=True, status__in=OPEN_STATUSES,
    ).exclude(pk__in=role_by_task_id).select_related('site__customer', 'task_type').annotate(
        priority_rank=PRIORITY_RANK,
    ).order_by('priority_rank', 'promised_at'))
    for task in unscheduled_responsible:
        task.my_role = 'responsible'
    unscheduled += unscheduled_responsible

    return days, unscheduled, _week_nav_context(today, start)


@login_required
def my_week(request):
    technician = require_technician(request)
    days, unscheduled, nav_context = _week_board(technician, request)
    context = {'days': days, 'unscheduled': unscheduled, **nav_context}
    return render(request, 'tasks/my_week.html', context)


@login_required
def my_progress(request):
    """Skills and conduct areas the technician is rated on, certification
    status, and standing among peers in the same country — plus counts for
    the current week and the plain reliability facts (tasks completed,
    on-time %) once there's enough volume to mean anything. No
    first-time-fix rate yet: per the doc's own build order, that needs
    months of real event data, not just a query.
    """
    technician = require_technician(request)

    skills = _skills_with_current_rating(technician)
    conduct_areas = _conduct_areas_with_current_rating(technician)
    certification = _certification_status(technician)
    ninety_day = _ninety_day_progress(technician, certification)
    reliability = _reliability_stats(technician)

    leaderboard = _leaderboard(technician.country)
    rank = next((position for position, (t, _points) in enumerate(leaderboard, start=1) if t.pk == technician.pk), None)

    _today, start, end = _week_window(request)
    my_assignments = TaskAssignment.objects.filter(technician=technician, is_active=True)

    assigned_count = Task.objects.filter(
        Q(pk__in=my_assignments.values('task_id')),
        Q(scheduled_for__date__range=(start, end))
        | (Q(scheduled_for__isnull=True) & Q(status__in=OPEN_STATUSES)),
    ).count()

    completed_count = TaskEvent.objects.filter(
        actor=request.user, event_type=TaskEvent.EventType.COMPLETED, occurred_at__date__range=(start, end),
    ).count()

    # Same rule as the supervisors' Hours page: a report's hours belong
    # to the lead who filed it.
    month_start, month_end = _month_window(timezone.localtime().date().replace(day=1))
    month_reports = list(
        WorkReport.objects.filter(
            submitted_at__gte=month_start, submitted_at__lt=month_end,
            task__assignments__technician=technician, task__assignments__role=TaskAssignment.Role.LEAD,
            task__assignments__is_active=True,
        ).select_related('task').distinct(),
    )

    context = {
        'month_hours': sum(report.labour_hours for report in month_reports),
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
    return render(request, 'tasks/my_progress.html', context)


@login_required
def my_skills(request):
    """Self-assessment — a technician's own first guess at each skill and
    conduct area, using the same rubric a supervisor later reviews it
    against. A self-rating never counts toward certification on its own,
    and once set it can only be changed by a supervisor from here on.
    """
    technician = require_technician(request)

    if request.method == 'POST':
        action = request.POST.get('action')
        today = timezone.localtime().date()

        if action == 'rate_skill':
            skill = get_object_or_404(Skill, pk=request.POST.get('skill_id'), is_active=True)
            form = SelfRateSkillForm(request.POST, request.FILES)
            if not TechnicianSkill.objects.filter(technician=technician, skill=skill).exists() and form.is_valid():
                level = int(form.cleaned_data['level'])
                evidence = form.cleaned_data['evidence']
                note = form.cleaned_data['note']
                with transaction.atomic():
                    TechnicianSkill.objects.create(
                        technician=technician, skill=skill, level=level, evidence=evidence,
                        source=TechnicianSkill.Source.SELF, set_by=technician, set_on=today, note=note,
                    )
                    # The same uploaded file gets saved twice (current
                    # snapshot + history log) — its read pointer is at EOF
                    # after the first save, so it must be rewound first or
                    # the assessment row would get an empty file.
                    evidence.seek(0)
                    TechnicianSkillAssessment.objects.create(
                        technician=technician, skill=skill, level=level, evidence=evidence,
                        source=TechnicianSkill.Source.SELF, set_by=technician, set_on=today, note=note,
                    )
                messages.success(request, _('Self-rating saved.'))
                return redirect('tasks:my_skills')

        elif action == 'rate_conduct':
            area = get_object_or_404(ConductArea, pk=request.POST.get('conduct_area_id'), is_active=True)
            form = SelfRateLevelForm(request.POST)
            if not TechnicianConduct.objects.filter(technician=technician, conduct_area=area).exists() and form.is_valid():
                level = int(form.cleaned_data['level'])
                with transaction.atomic():
                    TechnicianConduct.objects.create(
                        technician=technician, conduct_area=area, level=level,
                        source=TechnicianConduct.Source.SELF, set_by=technician, set_on=today,
                    )
                    TechnicianConductAssessment.objects.create(
                        technician=technician, conduct_area=area, level=level,
                        source=TechnicianConduct.Source.SELF, set_by=technician, set_on=today,
                    )
                messages.success(request, _('Self-rating saved.'))
                return redirect('tasks:my_skills')

    basic_skills, cardio_skills = _split_by_category(_skills_with_current_rating(technician))
    context = {
        'basic_skills': basic_skills,
        'cardio_skills': cardio_skills,
        'conduct_areas': _conduct_areas_with_current_rating(technician),
        'self_rate_skill_form': SelfRateSkillForm(),
        'self_rate_form': SelfRateLevelForm(),
    }
    return render(request, 'tasks/my_skills.html', context)


@login_required
def my_profile(request):
    """A technician's own account screen — the two fields of their own
    record that are genuinely theirs to set (photo, language), a password
    change, and a quick-glance snapshot of standing (certification,
    90-day track, country) that My progress covers in full detail.
    """
    technician = require_technician(request)

    if request.method == 'POST' and request.POST.get('action') == 'change_password':
        profile_form = MyProfileForm(instance=technician)
        password_form = PasswordChangeForm(request.user, request.POST)
        if password_form.is_valid():
            password_form.save()
            update_session_auth_hash(request, request.user)
            messages.success(request, _('Password changed.'))
            return redirect('tasks:my_profile')
    elif request.method == 'POST':
        password_form = PasswordChangeForm(request.user)
        profile_form = MyProfileForm(request.POST, request.FILES, instance=technician)
        if profile_form.is_valid():
            profile_form.save()
            messages.success(request, _('Profile updated.'))
            return redirect('tasks:my_profile')
    else:
        profile_form = MyProfileForm(instance=technician)
        password_form = PasswordChangeForm(request.user)

    certification = _certification_status(technician)
    leaderboard = _leaderboard(technician.country)
    rank = next((position for position, (t, _points) in enumerate(leaderboard, start=1) if t.pk == technician.pk), None)

    # No single assigned supervisor is tracked — just everyone in this
    # country a technician can actually go to, same country-scoping as
    # every other roster in the app.
    supervisors = Technician.objects.filter(
        country=technician.country, is_active=True,
        role__in=[Technician.Role.SUPERVISOR, Technician.Role.MANAGER, Technician.Role.ADMIN],
    ).order_by('role', 'full_name')

    context = {
        'technician': technician,
        'profile_form': profile_form,
        'password_form': password_form,
        'certification': certification,
        'ninety_day': _ninety_day_progress(technician, certification),
        'rank': rank,
        'leaderboard_size': len(leaderboard),
        'supervisors': supervisors,
    }
    return render(request, 'tasks/my_profile.html', context)


@login_required
def technician_create(request):
    """Admin-only — creating a technician record means choosing its role,
    and a role is a privilege grant (whoever creates its login next
    inherits whatever role is already sitting on the record). Letting
    manage_technicians alone reach this — a permission the supervisor
    role has by default — would let a supervisor plant a technician
    record pre-set to manager or admin for someone else to unknowingly
    hand a login to later.
    """
    require_admin(request)

    if request.method == 'POST':
        form = TechnicianCreateForm(request.POST)
        if form.is_valid():
            technician = form.save(commit=False)
            technician.country = get_active_country(request)
            technician.save()
            messages.success(request, _('Technician added.'))
            return redirect('tasks:technician_list')
    else:
        form = TechnicianCreateForm()

    return render(request, 'tasks/technician_create.html', {'form': form})


@login_required
def technician_list(request):
    """The technician roster for a supervisor's own country — the "who can
    I rely on" view. Never existed as a screen before this feature.

    Shows active technicians by default; ?status=inactive switches to the
    deactivated ones instead (never both — an inactive technician doesn't
    belong on the "who can I rely on" list), so a manager can find someone
    to reactivate without them cluttering the roster everyone else uses.
    """
    require_permission(request, RolePermission.Permission.VIEW_TECHNICIANS)

    if request.method == 'POST' and request.POST.get('action') == 'reactivate':
        require_permission(request, RolePermission.Permission.MANAGE_TECHNICIANS)
        technician = get_object_or_404(
            Technician, pk=request.POST.get('technician_id'), country=get_active_country(request),
        )
        technician.set_active(True)
        messages.success(request, _('Technician reactivated.'))
        return redirect(f"{reverse('tasks:technician_list')}?status=inactive")

    show_inactive = request.GET.get('status') == 'inactive'
    search = request.GET.get('q', '').strip()
    technicians = Technician.objects.filter(
        is_active=not show_inactive, country=get_active_country(request),
    ).order_by('full_name')
    if search:
        technicians = technicians.filter(full_name__icontains=search)

    rows = []
    for technician in technicians:
        certification = _certification_status(technician)
        rows.append({
            'technician': technician,
            'certification': certification,
            'ninety_day': _ninety_day_progress(technician, certification),
        })

    context = {'rows': rows, 'search': search, 'show_inactive': show_inactive}
    return render(request, 'tasks/technician_list.html', context)


@login_required
def all_technicians(request):
    """Every active technician in every country, with the same
    certification/90-day columns as the regular roster — a manager's
    combined "everyone, everywhere" roster and certification-progress
    overview, so auditing someone doesn't mean switching active country
    first. Manager-only, same fixed floor as the other "all ..." boards.
    """
    require_manager(request)

    search = request.GET.get('q', '').strip()
    technicians = Technician.objects.filter(is_active=True).select_related('country').order_by('full_name')
    if search:
        technicians = technicians.filter(full_name__icontains=search)

    rows = []
    for technician in technicians:
        certification = _certification_status(technician)
        rows.append({
            'technician': technician,
            'certification': certification,
            'ninety_day': _ninety_day_progress(technician, certification),
        })

    return render(request, 'tasks/all_technicians.html', {'rows': rows, 'search': search})


def _technician_hours_rows(active_country, period_start_date):
    """One row per active technician in the country: the work reports they
    filed as lead that month, their hours, estimate and overruns, and how
    many reported tasks they helped on. Shared by the Hours page and its
    CSV export.
    """
    period_start, period_end = _month_window(period_start_date)
    reports = WorkReport.objects.filter(
        submitted_at__gte=period_start, submitted_at__lt=period_end, task__site__customer__country=active_country,
    ).select_related('task__site__customer').prefetch_related(
        Prefetch(
            'task__assignments', queryset=TaskAssignment.objects.filter(is_active=True), to_attr='active_assignments',
        ),
    ).order_by('submitted_at')

    leads_reports = defaultdict(list)
    helped_on = defaultdict(int)
    for report in reports:
        for assignment in report.task.active_assignments:
            if assignment.role == TaskAssignment.Role.LEAD:
                leads_reports[assignment.technician_id].append(report)
            else:
                helped_on[assignment.technician_id] += 1

    rows = []
    for technician in Technician.objects.filter(is_active=True, country=active_country).order_by('full_name'):
        own_reports = leads_reports[technician.pk]
        rows.append({
            'technician': technician,
            'reports': own_reports,
            'hours': sum(report.labour_hours for report in own_reports),
            'estimated': sum(report.task.estimated_hours for report in own_reports if report.task.estimated_hours),
            'helped_on': helped_on[technician.pk],
            'overrun_count': sum(1 for report in own_reports if report.is_overrun),
        })
    return rows


@login_required
def technician_hours(request):
    """Labour hours from filed work reports, per technician, for one month
    in the active country — how a supervisor/manager/admin follows each
    technician's actual workload. A report's hours count toward the lead
    who filed it (the report covers the whole job), so the team total
    never double-counts; helpers get a "helped on" count instead. Every
    active technician gets a row, so someone with nothing filed shows up
    as zero rather than going unnoticed.
    """
    require_permission(request, RolePermission.Permission.VIEW_TECHNICIANS)
    period_start_date = _resolve_report_month(request)
    rows = _technician_hours_rows(get_active_country(request), period_start_date)

    context = {
        'rows': rows,
        'total_hours': sum(row['hours'] for row in rows),
        'total_reports': sum(len(row['reports']) for row in rows),
        'period_start': period_start_date,
        'prev_month': _shift_month(period_start_date, -1),
        'next_month': _shift_month(period_start_date, 1),
        'this_month': timezone.localtime().date().replace(day=1),
    }
    return render(request, 'tasks/technician_hours.html', context)


@login_required
def technician_hours_export(request):
    """The Hours page as CSV — a per-technician summary, then every report
    behind it, for payroll/month-end. Same permission and country scope.
    """
    require_permission(request, RolePermission.Permission.VIEW_TECHNICIANS)
    active_country = get_active_country(request)
    period_start_date = _resolve_report_month(request)
    rows = _technician_hours_rows(active_country, period_start_date)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = (
        f'attachment; filename="technician-hours-{active_country.iso_code}-{period_start_date:%Y-%m}.csv"'
    )
    writer = csv.writer(response)

    writer.writerow([f'Technician hours — {active_country.name} — {period_start_date:%B %Y}'])
    writer.writerow(['Technician', 'Reports filed', 'Labour hours', 'Estimated hours', 'Well over estimate', 'Helped on'])
    for row in rows:
        writer.writerow([
            row['technician'].full_name, len(row['reports']), row['hours'], row['estimated'] or '',
            row['overrun_count'], row['helped_on'],
        ])

    writer.writerow([])
    writer.writerow(['Reports'])
    writer.writerow([
        'Technician', 'Task number', 'Customer', 'Submitted at', 'Labour hours', 'Estimated hours',
        'Well over estimate',
    ])
    for row in rows:
        for report in row['reports']:
            writer.writerow([
                row['technician'].full_name, report.task.task_number, report.task.site.customer.name,
                timezone.localtime(report.submitted_at).strftime('%Y-%m-%d %H:%M'), report.labour_hours,
                report.task.estimated_hours or '', 'yes' if report.is_overrun else '',
            ])

    return response


@login_required
def technician_board(request, pk):
    """A supervisor's view of one technician's week — same shape as
    my_week, just for someone else, with the same country scoping used
    everywhere else a supervisor looks at a specific technician.
    """
    requesting_technician = require_permission(request, RolePermission.Permission.VIEW_TECHNICIANS)
    technician = _scoped_or_404(
        Technician.objects, pk, requesting_technician, get_active_country(request), 'country',
    )

    days, unscheduled, nav_context = _week_board(technician, request)
    context = {'technician': technician, 'days': days, 'unscheduled': unscheduled, **nav_context}
    return render(request, 'tasks/technician_board.html', context)


@login_required
def machine_list(request):
    """Every machine on file for this country — one row per site visit's
    worth of equipment, created the first time a technician records it on
    a report. Same office-wide reach as the technician roster: any
    supervisor, manager, support manager, or admin can look one up, not
    just whoever happens to be viewing that customer's own site list.
    """
    require_permission(request, RolePermission.Permission.VIEW_MACHINES)
    active_country = get_active_country(request)

    search = request.GET.get('q', '').strip()
    assets = Asset.objects.filter(site__customer__country=active_country).select_related(
        'site__customer', 'brand',
    ).order_by('site__customer__name', 'site__name', 'model_name')
    if search:
        assets = assets.filter(
            Q(serial_no__icontains=search) | Q(model_name__icontains=search)
            | Q(brand__name__icontains=search) | Q(site__name__icontains=search)
            | Q(site__customer__name__icontains=search)
        )

    paginator = Paginator(assets, 25)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'tasks/machine_list.html', {'page_obj': page_obj, 'search': search})


@login_required
def machine_detail(request, pk):
    """One machine's full history — every task it's ever been recorded
    against (via task_asset), and the ticket behind each of those, for
    whichever ones started life as a customer complaint rather than an
    office-scheduled visit. There's no direct ticket-to-asset link (a
    ticket only ever names a serial as free text, see
    Task.reported_serial_numbers), so a ticket only shows up here once
    its task actually recorded this specific machine.
    """
    requesting_technician = require_permission(request, RolePermission.Permission.VIEW_MACHINES)
    asset = _scoped_or_404(
        Asset.objects.select_related('site__customer__country', 'brand'),
        pk, requesting_technician, get_active_country(request), 'site__customer__country',
    )

    task_assets = asset.task_assets.select_related(
        'task__site', 'task__ticket',
    ).order_by('-task__reported_at')

    context = {'asset': asset, 'task_assets': task_assets}
    return render(request, 'tasks/machine_detail.html', context)


@login_required
def technician_skills(request, pk):
    """A supervisor's review screen for one technician — confirm or
    override every self-rating against real evidence. A level only ever
    changes here, by a deliberate supervisor action; nothing computed
    writes to it automatically.
    """
    supervisor = require_permission(request, RolePermission.Permission.REVIEW_SKILLS)
    technician = _scoped_or_404(Technician.objects, pk, supervisor, get_active_country(request), 'country')

    if request.method == 'POST':
        action = request.POST.get('action')
        today = timezone.localtime().date()

        if action == 'review_skill':
            skill = get_object_or_404(Skill, pk=request.POST.get('skill_id'), is_active=True)
            form = ReviewLevelForm(request.POST)
            if form.is_valid():
                level = int(form.cleaned_data['level'])
                note = form.cleaned_data['note']
                with transaction.atomic():
                    TechnicianSkill.objects.update_or_create(
                        technician=technician, skill=skill,
                        defaults={
                            'level': level, 'source': TechnicianSkill.Source.SUPERVISOR,
                            'set_by': supervisor, 'set_on': today, 'note': note,
                        },
                    )
                    TechnicianSkillAssessment.objects.create(
                        technician=technician, skill=skill, level=level,
                        source=TechnicianSkill.Source.SUPERVISOR, set_by=supervisor, set_on=today, note=note,
                    )
                messages.success(request, _('Level confirmed.'))
                return redirect('tasks:technician_skills', pk=technician.pk)

        elif action == 'review_conduct':
            area = get_object_or_404(ConductArea, pk=request.POST.get('conduct_area_id'), is_active=True)
            form = ReviewLevelForm(request.POST)
            if form.is_valid():
                level = int(form.cleaned_data['level'])
                note = form.cleaned_data['note']
                with transaction.atomic():
                    TechnicianConduct.objects.update_or_create(
                        technician=technician, conduct_area=area,
                        defaults={
                            'level': level, 'source': TechnicianConduct.Source.SUPERVISOR,
                            'set_by': supervisor, 'set_on': today, 'note': note,
                        },
                    )
                    TechnicianConductAssessment.objects.create(
                        technician=technician, conduct_area=area, level=level,
                        source=TechnicianConduct.Source.SUPERVISOR, set_by=supervisor, set_on=today, note=note,
                    )
                messages.success(request, _('Level confirmed.'))
                return redirect('tasks:technician_skills', pk=technician.pk)

    negligence_events = None
    if supervisor.is_manager_tier:
        # Manager-tier reliability signal — a supervisor reviewing the
        # same technician's skills never sees it, same as on the task
        # itself (see task_detail).
        negligence_events = TaskEvent.objects.filter(
            event_type=TaskEvent.EventType.NEGLIGENCE, task__assignments__technician=technician,
            task__assignments__role=TaskAssignment.Role.LEAD, task__assignments__is_active=True,
        ).select_related('task').order_by('-occurred_at')

    basic_skills, cardio_skills = _split_by_category(_skills_with_current_rating(technician))
    context = {
        'technician': technician,
        'basic_skills': basic_skills,
        'cardio_skills': cardio_skills,
        'conduct_areas': _conduct_areas_with_current_rating(technician),
        'review_form': ReviewLevelForm(),
        'certification': _certification_status(technician),
        'negligence_events': negligence_events,
        'reliability': _reliability_stats(technician),
    }
    return render(request, 'tasks/technician_skills.html', context)


@login_required
def technician_edit(request, pk):
    """A supervisor or manager managing someone else's record, from the
    roster. Manager-only fields (role, employment details, country, ...),
    plus deactivation, creating a login, and a password reset, are gated
    by is_manager; MANAGE_TECHNICIANS itself still covers everyone else's
    edit (name/phone/language/email/photo). A supervisor stays scoped to
    their own active country; a manager can reach any technician, since
    that's the point of the all_technicians board this now also links
    from.
    """
    requesting_technician = require_permission(request, RolePermission.Permission.MANAGE_TECHNICIANS)
    is_manager = requesting_technician.is_manager_tier
    is_admin = requesting_technician.role == Technician.Role.ADMIN
    technician = _scoped_or_404(
        Technician.objects, pk, requesting_technician, get_active_country(request), 'country',
    )

    form = TechnicianEditForm(instance=technician, is_manager=is_manager, is_admin=is_admin)
    deactivate_form = DeactivateTechnicianForm()
    login_form = CreateTechnicianLoginForm() if is_manager and technician.user_id is None else None
    password_form = SetPasswordForm(user=technician.user) if is_manager and technician.user_id else None

    if request.method == 'POST' and request.POST.get('action') == 'deactivate' and is_manager:
        deactivate_form = DeactivateTechnicianForm(request.POST)
        if deactivate_form.is_valid():
            technician.set_active(False, reason=deactivate_form.cleaned_data['reason'])
            messages.success(request, _('Technician deactivated.'))
            return redirect('tasks:technician_list')

    elif request.method == 'POST' and request.POST.get('action') == 'create_login' and login_form is not None:
        login_form = CreateTechnicianLoginForm(request.POST)
        if login_form.is_valid():
            email = login_form.cleaned_data['email']
            password = _generate_temporary_password()
            user = get_user_model().objects.create_user(username=email, email=email, password=password)
            technician.user = user
            technician.must_change_password = True
            technician.save(update_fields=['user', 'must_change_password'])
            send_technician_login_email(request, technician, password)
            messages.success(request, _('Login created and emailed to %(email)s.') % {'email': email})
            return redirect('tasks:technician_edit', pk=technician.pk)

    elif request.method == 'POST' and request.POST.get('action') == 'reset_password' and password_form is not None:
        password_form = SetPasswordForm(user=technician.user, data=request.POST)
        if password_form.is_valid():
            password_form.save()
            messages.success(request, _('Password reset.'))
            return redirect('tasks:technician_edit', pk=technician.pk)

    elif request.method == 'POST':
        form = TechnicianEditForm(
            request.POST, request.FILES, instance=technician, is_manager=is_manager, is_admin=is_admin,
        )
        if form.is_valid():
            form.save()
            messages.success(request, _('Technician updated.'))
            return redirect('tasks:technician_list')

    context = {
        'technician': technician, 'form': form, 'deactivate_form': deactivate_form,
        'login_form': login_form, 'password_form': password_form, 'is_manager': is_manager,
    }
    return render(request, 'tasks/technician_edit.html', context)


@login_required
def first_login(request):
    """A one-time stop between signing in on a temporary password and
    reaching the dashboard — just setting a real password. Everything
    else about a technician's own record stays editable any time from
    My profile, not gated here.
    """
    technician = require_technician(request)
    if not technician.must_change_password:
        return redirect('tasks:dashboard')

    if request.method == 'POST':
        form = TechnicianFirstLoginForm(request.POST, user=request.user)
        if form.is_valid():
            technician.must_change_password = False
            technician.save(update_fields=['must_change_password'])
            request.user.set_password(form.cleaned_data['new_password1'])
            request.user.save(update_fields=['password'])
            update_session_auth_hash(request, request.user)
            messages.success(request, _('Password set — welcome!'))
            return redirect('tasks:dashboard')
    else:
        form = TechnicianFirstLoginForm(user=request.user)

    return render(request, 'tasks/first_login.html', {'form': form})


@login_required
def set_active_country(request):
    """Manager-only header switcher — session-only, never touches the
    manager's own technician.country. POST only, since this changes what
    every other screen shows for the rest of the session.
    """
    require_manager(request)

    if request.method == 'POST':
        country = get_object_or_404(Country, pk=request.POST.get('country'))
        request.session[ACTIVE_COUNTRY_SESSION_KEY] = country.pk
        messages.success(request, _('Now viewing %(country)s.') % {'country': country.display_name})

    next_url = request.POST.get('next', '')
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = reverse('tasks:dashboard')
    return redirect(next_url)


@login_required
def role_permissions(request):
    """Admin-only: which role can do what, plus global notification
    behavior. Deliberately not gated by the configurable system it
    manages (require_admin, not require_permission) — otherwise a bad
    edit here could lock every role out of ever fixing it again. Also
    the one screen a manager doesn't get, unlike every other manager-tier
    screen (see require_admin).
    """
    require_admin(request)

    roles = [
        Technician.Role.TECHNICIAN, Technician.Role.SUPERVISOR, Technician.Role.MANAGER,
        Technician.Role.SUPPORT_MANAGER, Technician.Role.WAREHOUSE_MANAGER, Technician.Role.ADMIN,
    ]
    permissions = list(RolePermission.Permission)
    notification_settings = NotificationSettings.load()

    if request.method == 'POST':
        if request.POST.get('action') == 'save_notifications':
            notification_settings.auto_notify_on_reschedule = 'auto_notify_on_reschedule' in request.POST
            notification_settings.save(update_fields=['auto_notify_on_reschedule'])
            messages.success(request, _('Notification settings updated.'))
            return redirect('tasks:role_permissions')

        for permission in permissions:
            for role in roles:
                RolePermission.objects.update_or_create(
                    role=role, permission=permission,
                    defaults={'allowed': f'{role}__{permission}' in request.POST},
                )
        messages.success(request, _('Permissions updated.'))
        return redirect('tasks:role_permissions')

    allowed_pairs = set(RolePermission.objects.filter(allowed=True).values_list('role', 'permission'))
    matrix = [
        {
            'permission': permission,
            'cells': [
                {
                    'field_name': f'{role}__{permission}',
                    'allowed': (role, permission) in allowed_pairs,
                }
                for role in roles
            ],
        }
        for permission in permissions
    ]

    context = {'roles': roles, 'matrix': matrix, 'notification_settings': notification_settings}
    return render(request, 'tasks/role_permissions.html', context)


@login_required
def audit_log(request):
    """Who did what, anywhere in Django admin — add/change/delete, with
    the actor and a timestamp, for free from Django's own LogEntry.
    Admin-only, same reasoning as Roles & permissions: this is oversight
    of everyone, including other admins, so it stays off the manager-tier
    floor. Covers /admin/ actions only — the in-app screens' own actions
    already have their own trail on each task (see TaskEvent).
    """
    require_admin(request)

    entries = LogEntry.objects.select_related('user', 'content_type').order_by('-action_time')
    paginator = Paginator(entries, 50)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'tasks/audit_log.html', {'page_obj': page_obj})


def _shift_month(first_of_month, delta):
    month_index = first_of_month.month - 1 + delta
    year = first_of_month.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _month_window(first_of_month):
    """Aware [start, end) datetimes covering one calendar month."""
    start = timezone.make_aware(datetime.combine(first_of_month, datetime.min.time()))
    end = timezone.make_aware(datetime.combine(_shift_month(first_of_month, 1), datetime.min.time()))
    return start, end


def _resolve_report_month(request):
    """The first-of-month date this report covers, from ?month=YYYY-MM,
    defaulting to the current month. Always a clean first-of-month date —
    never trusts the query string past that, so a malformed value just
    falls back instead of erroring.
    """
    today = timezone.localtime().date()
    try:
        year, month = (int(part) for part in request.GET.get('month', '').split('-'))
        return date(year, month, 1)
    except (TypeError, ValueError):
        return today.replace(day=1)


def _month_report_querysets(period_start_date):
    """Every ticket and task received in this month, company-wide — not
    scoped to any active country, since this report is for leadership,
    not one country's board.
    """
    period_start, period_end = _month_window(period_start_date)
    tickets = CustomerTicket.objects.filter(
        submitted_at__gte=period_start, submitted_at__lt=period_end,
    ).select_related('country').order_by('submitted_at')
    tasks = Task.objects.filter(
        reported_at__gte=period_start, reported_at__lt=period_end,
    ).select_related('site__customer', 'site__customer__country').order_by('reported_at')
    return tickets, tasks


@login_required
def monthly_report(request):
    """Every ticket and task received in a given month, company-wide,
    with status — the report a manager pulls together each month for
    leadership. Manager-tier, not gated by view_tasks/manage_tickets:
    it's a cross-country summary, same fixed-floor reasoning as the
    all_* boards.
    """
    require_manager(request)

    period_start_date = _resolve_report_month(request)
    tickets, tasks = _month_report_querysets(period_start_date)

    ticket_status_labels = dict(CustomerTicket.Status.choices)
    task_status_labels = dict(Task.Status.choices)

    context = {
        'period_start': period_start_date,
        'prev_month': _shift_month(period_start_date, -1),
        'next_month': _shift_month(period_start_date, 1),
        'this_month': timezone.localtime().date().replace(day=1),
        'tickets': tickets,
        'tasks': tasks,
        'ticket_status_counts': [
            {'label': ticket_status_labels.get(row['status'], row['status']), 'count': row['count']}
            for row in tickets.values('status').annotate(count=Count('id')).order_by('status')
        ],
        'task_status_counts': [
            {'label': task_status_labels.get(row['status'], row['status']), 'count': row['count']}
            for row in tasks.values('status').annotate(count=Count('id')).order_by('status')
        ],
        'ticket_total': tickets.count(),
        'task_total': tasks.count(),
    }
    return render(request, 'tasks/monthly_report.html', context)


@login_required
def monthly_report_export(request):
    require_manager(request)

    period_start_date = _resolve_report_month(request)
    tickets, tasks = _month_report_querysets(period_start_date)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="monthly-report-{period_start_date:%Y-%m}.csv"'
    writer = csv.writer(response)

    writer.writerow([f'Tickets received — {period_start_date:%B %Y}'])
    writer.writerow(['Country', 'Company', 'Site description', 'Status', 'Submitted at'])
    for ticket in tickets:
        writer.writerow([
            ticket.country.name, ticket.company_name, ticket.site_description,
            ticket.get_status_display(), timezone.localtime(ticket.submitted_at),
        ])

    writer.writerow([])
    writer.writerow([f'Tasks received — {period_start_date:%B %Y}'])
    writer.writerow(['Task number', 'Country', 'Customer', 'Site', 'Status', 'Reported at'])
    for task in tasks:
        writer.writerow([
            task.task_number, task.site.customer.country.name, task.site.customer.name, task.site.name,
            task.get_status_display(), timezone.localtime(task.reported_at),
        ])

    return response


@login_required
def skill_list(request):
    """Every active repair-task skill a technician can be rated on —
    manager-only, same fixed-floor reasoning as role_permissions: skills
    are global reference data, not scoped to any country, and letting
    the screen that manages them be subject to its own permission row
    would risk a bad edit locking every role out of fixing it.
    """
    require_manager(request)
    basic_skills, cardio_skills = _split_by_category(Skill.objects.filter(is_active=True))
    conduct_areas = ConductArea.objects.filter(is_active=True)
    return render(request, 'tasks/skill_list.html', {
        'basic_skills': basic_skills, 'cardio_skills': cardio_skills, 'conduct_areas': conduct_areas,
    })


@login_required
def skill_create(request):
    require_manager(request)

    if request.method == 'POST':
        form = SkillCreateForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _('Skill added.'))
            return redirect('tasks:skill_list')
    else:
        form = SkillCreateForm()

    return render(request, 'tasks/skill_create.html', {'form': form})


@login_required
def conduct_area_create(request):
    require_manager(request)

    if request.method == 'POST':
        form = ConductAreaCreateForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _('Conduct area added.'))
            return redirect('tasks:skill_list')
    else:
        form = ConductAreaCreateForm()

    return render(request, 'tasks/conduct_area_create.html', {'form': form})


@login_required
def brand_list(request):
    """Every active equipment brand/vendor — manager-only, same fixed-floor
    reasoning as skill_list: brands are global reference data, not scoped
    to any country.
    """
    require_manager(request)
    brands = Brand.objects.filter(is_active=True)
    return render(request, 'tasks/brand_list.html', {'brands': brands})


@login_required
def brand_create(request):
    require_manager(request)

    if request.method == 'POST':
        form = BrandCreateForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _('Brand added.'))
            return redirect('tasks:brand_list')
    else:
        form = BrandCreateForm()

    return render(request, 'tasks/brand_create.html', {'form': form})


@login_required
def country_list(request):
    """Every country the roster and every other per-country screen can
    scope to — manager-only, same fixed-floor reasoning as skill_list.
    Shows both active and inactive, since toggling and deleting both
    happen from here.
    """
    require_manager(request)

    if request.method == 'POST':
        country = get_object_or_404(Country, pk=request.POST.get('country_id'))
        action = request.POST.get('action')
        if action == 'toggle_active':
            country.is_active = not country.is_active
            country.save(update_fields=['is_active'])
            messages.success(request, _('Country updated.'))
        elif action == 'delete':
            try:
                country.delete()
                messages.success(request, _('Country deleted.'))
            except ProtectedError:
                messages.error(
                    request,
                    _(
                        '“%(name)s” still has technicians, customers, or tickets attached to it — '
                        'deactivate it instead of deleting.'
                    ) % {'name': country.display_name},
                )
        return redirect('tasks:country_list')

    countries = Country.objects.order_by('name')
    return render(request, 'tasks/country_list.html', {'countries': countries})


@login_required
def country_create(request):
    require_manager(request)

    if request.method == 'POST':
        form = CountryCreateForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _('Country added.'))
            return redirect('tasks:country_list')
    else:
        form = CountryCreateForm()

    return render(request, 'tasks/country_create.html', {'form': form})


@login_required
def my_task_detail(request, pk):
    technician = require_technician(request)

    assignment = get_object_or_404(
        TaskAssignment.objects.select_related(
            'task__site__customer', 'task__task_type', 'task__brand', 'task__required_skill',
        ),
        task__pk=pk, technician=technician, is_active=True,
    )
    task = assignment.task
    is_lead = assignment.role == TaskAssignment.Role.LEAD
    next_action = _next_technician_action(task) if is_lead else None
    is_paused = is_lead and task.status == Task.Status.IN_PROGRESS and _task_is_paused(task)
    can_block = is_lead and task.status in BLOCKABLE_STATUSES and not is_paused
    can_pause = is_lead and task.status == Task.Status.IN_PROGRESS and not is_paused
    can_resume = is_paused
    undoable = undoable_tap(task, request.user) if is_lead else None

    TaskMessageRecipient.objects.filter(
        message__task=task, technician=technician, seen_at__isnull=True,
    ).update(seen_at=timezone.now())

    upload_form = TaskAttachmentUploadForm()
    block_form = BlockTaskForm()
    pause_form = PauseTaskForm()
    message_form = TaskMessageForm()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_message':
            message_form = TaskMessageForm(request.POST)
            if message_form.is_valid():
                _post_task_message(request, task, message_form.cleaned_data['message'])
                messages.success(request, _('Message sent.'))
                return redirect('tasks:my_task_detail', pk=task.pk)

        elif action in TECHNICIAN_ACTIONS and action == next_action:
            event_type, new_status = TECHNICIAN_ACTIONS[action]
            with transaction.atomic():
                TaskEvent.objects.create(
                    task=task, event_type=event_type, occurred_at=timezone.now(), actor=request.user,
                )
                if new_status:
                    task.status = new_status
                    task.save(update_fields=['status'])
            messages.success(request, _('Updated.'))
            return redirect('tasks:my_task_detail', pk=task.pk)

        elif action == 'block' and can_block:
            block_form = BlockTaskForm(request.POST)
            if block_form.is_valid():
                with transaction.atomic():
                    task.status = Task.Status.BLOCKED
                    task.save(update_fields=['status'])
                    TaskEvent.objects.create(
                        task=task, event_type=TaskEvent.EventType.BLOCKED, occurred_at=timezone.now(),
                        actor=request.user, note=block_form.cleaned_data['note'],
                    )
                messages.success(request, _('Task marked blocked.'))
                return redirect('tasks:my_task_detail', pk=task.pk)

        elif action == 'pause' and can_pause:
            pause_form = PauseTaskForm(request.POST)
            if pause_form.is_valid():
                TaskEvent.objects.create(
                    task=task, event_type=TaskEvent.EventType.PAUSED, occurred_at=timezone.now(),
                    actor=request.user, note=pause_form.cleaned_data['note'],
                )
                messages.success(request, _('Marked stopped for today.'))
                return redirect('tasks:my_task_detail', pk=task.pk)

        elif action == 'resume' and can_resume:
            TaskEvent.objects.create(
                task=task, event_type=TaskEvent.EventType.RESUMED, occurred_at=timezone.now(), actor=request.user,
            )
            messages.success(request, _('Marked started again.'))
            return redirect('tasks:my_task_detail', pk=task.pk)

        elif action == 'undo' and is_lead:
            if undo_last_tap(task, request.user):
                messages.success(request, _('Undone.'))
            else:
                messages.error(request, _('That can no longer be undone — ask a manager to correct the time.'))
            return redirect('tasks:my_task_detail', pk=task.pk)

        elif action == 'upload':
            upload_form = TaskAttachmentUploadForm(request.POST, request.FILES)
            if upload_form.is_valid():
                _save_attachment(
                    request, task, upload_form.cleaned_data['file'], upload_form.cleaned_data['purpose'],
                )
                messages.success(request, _('Photo added.'))
                return redirect('tasks:my_task_detail', pk=task.pk)

    teammates = task.assignments.filter(is_active=True).exclude(technician=technician).select_related('technician')
    created_by_technician = getattr(task.created_by, 'technician', None)

    context = {
        'task': task,
        'is_lead': is_lead,
        'next_action': next_action,
        'can_block': can_block,
        'can_pause': can_pause,
        'can_resume': can_resume,
        # Block/pause/resume all matter most exactly when there's no
        # next_action — the task is IN_PROGRESS and just being worked —
        # so the section can't be gated on next_action alone.
        'show_status_section': bool(next_action) or can_block or can_pause or can_resume or bool(undoable),
        'undoable_tap': undoable,
        'can_file_report': is_lead and task.status in REPORT_EDITABLE_STATUSES and not is_paused,
        'report': getattr(task, 'report', None),
        'attachments': task.attachments.select_related('uploaded_by'),
        # Negligence flags are a manager-only reliability signal — never
        # shown to the technician being flagged.
        'events': task.events.exclude(event_type=TaskEvent.EventType.NEGLIGENCE).order_by('occurred_at'),
        'upload_form': upload_form,
        'block_form': block_form,
        'pause_form': pause_form,
        # Who else is on this task, and who created it — a technician
        # never saw either before, even when the creator is the manager.
        'teammates': teammates,
        'created_by_name': created_by_technician.full_name if created_by_technician else task.created_by.get_username(),
        'created_by_role_display': created_by_technician.get_role_display() if created_by_technician else '',
        'task_messages': task.messages.select_related('sent_by__technician'),
        'message_form': message_form,
    }
    return render(request, 'tasks/my_task_detail.html', context)


def _requires_signature(task):
    return bool(task.task_type and task.task_type.requires_signature)


def save_work_report(request, task, technician, report_form, part_rows):
    """Saves a validated WorkReportForm plus its parts and moves the task
    on — shared by the web form below and the mobile API, so both follow
    the same status rules. Call inside transaction.atomic(). Sets
    `first_submission` on the returned report (True if this filing moved
    the task to awaiting approval, False for a later correction).
    """
    saved_report = report_form.save(commit=False)
    saved_report.task = task
    saved_report.submitted_at = timezone.now()
    signature = report_form.cleaned_data.get('signature')
    if signature:
        path = default_storage.save(f'signatures/{task.pk}/{signature.name}', signature)
        saved_report.signature_url = request.build_absolute_uri(default_storage.url(path))
    saved_report.save()

    saved_report.parts_used.all().delete()
    for cleaned in part_rows:
        if cleaned.get('part_code') and cleaned.get('quantity') and cleaned.get('unit_cost'):
            PartUsed.objects.create(
                report=saved_report, part_code=cleaned['part_code'],
                description=cleaned.get('description', ''), quantity=cleaned['quantity'],
                unit_cost=cleaned['unit_cost'], currency_code=cleaned['currency_code'],
            )

    now = timezone.now()
    TaskEvent.objects.create(
        task=task, event_type=TaskEvent.EventType.REPORT_SUBMITTED,
        occurred_at=now, actor=request.user,
    )
    saved_report.first_submission = task.status not in (
        Task.Status.PENDING_SUPERVISOR_REVIEW, Task.Status.COMPLETED, Task.Status.CLOSED,
    )
    if saved_report.first_submission:
        # A technician's own report needs their supervisor's sign-off
        # before a manager ever sees it; a supervisor filing it themselves
        # (they're the lead on some tasks too) skips straight to the
        # manager, same as before this step existed.
        task.status = (
            Task.Status.PENDING_SUPERVISOR_REVIEW if technician.role == Technician.Role.TECHNICIAN
            else Task.Status.COMPLETED
        )
        task.save(update_fields=['status'])
        TaskEvent.objects.create(
            task=task, event_type=TaskEvent.EventType.COMPLETED,
            occurred_at=now, actor=request.user,
        )
    return saved_report


def report_saved_message(first_submission, task):
    if first_submission and task.status == Task.Status.PENDING_SUPERVISOR_REVIEW:
        return _('Report submitted — awaiting your supervisor’s review.')
    if first_submission:
        return _('Report submitted — awaiting manager approval before the task closes.')
    if task.status in (Task.Status.PENDING_SUPERVISOR_REVIEW, Task.Status.COMPLETED):
        return _('Report updated — still awaiting approval.')
    return _('Report updated.')


@login_required
def my_report_form(request, pk):
    """Filing this report is the lead's own last step — there's no
    separate "mark complete" tap. Saving it (first time or a later
    correction) marks the task completed; only a manager approving it
    from task detail actually closes it. Correcting a report still
    awaiting approval just re-saves it, without moving status backward or
    re-firing the completed event; once closed, it's a manager's to
    correct (task_report_correct).
    """
    technician = require_technician(request)

    assignment = get_object_or_404(
        TaskAssignment.objects.select_related('task__site__customer__country'),
        task__pk=pk, technician=technician, is_active=True, role=TaskAssignment.Role.LEAD,
    )
    task = assignment.task

    if task.status == Task.Status.CLOSED:
        messages.error(request, _('This task is closed — only a manager or admin can correct its report now.'))
        return redirect('tasks:my_task_detail', pk=task.pk)

    if task.status not in REPORT_EDITABLE_STATUSES:
        messages.error(request, _('Start work on this task before filing a report.'))
        return redirect('tasks:my_task_detail', pk=task.pk)

    if task.status == Task.Status.IN_PROGRESS and _task_is_paused(task):
        messages.error(request, _('Mark yourself started again before filing the report.'))
        return redirect('tasks:my_task_detail', pk=task.pk)

    return _report_form(request, task, technician, reverse('tasks:my_task_detail', args=[task.pk]))


@login_required
def task_report_correct(request, pk):
    """A manager or admin correcting a filed report — including after the
    task closed, when the lead no longer can. Same form and save path as
    the lead's own; a correction never moves the task's status (only a
    first filing does), and the history records who saved it.
    """
    manager = require_manager(request)
    task = _scoped_or_404(
        Task.objects.select_related('site__customer__country', 'report'),
        pk, manager, get_active_country(request), 'site__customer__country',
    )
    if getattr(task, 'report', None) is None or task.status not in MANAGER_REPORT_CORRECTABLE_STATUSES:
        messages.error(request, _('This task has no filed report to correct.'))
        return redirect('tasks:task_detail', pk=task.pk)
    return _report_form(request, task, manager, reverse('tasks:task_detail', args=[task.pk]))


def _report_form(request, task, technician, back_url):
    """The report form itself (report, machines, parts), shared by the
    lead filing it and a manager correcting it. `back_url` is where both
    the back link and a successful save go.
    """
    report = getattr(task, 'report', None)
    task_assets = task.task_assets.select_related('asset__brand')
    parts = report.parts_used.all() if report else PartUsed.objects.none()

    existing_assets = list(Asset.objects.filter(site=task.site))
    ExistingAssetFormSet = formset_factory(ExistingAssetOutcomeForm, extra=EXISTING_ASSET_ROWS)
    NewAssetFormSet = formset_factory(NewAssetForm, extra=NEW_ASSET_ROWS)
    PartFormSet = formset_factory(PartUsedItemForm, extra=PART_ROWS)

    existing_initial = [{'asset': ta.asset_id, 'outcome': ta.outcome} for ta in task_assets]
    part_initial = [
        {
            'part_code': part.part_code, 'description': part.description, 'quantity': part.quantity,
            'unit_cost': part.unit_cost, 'currency_code': part.currency_code,
        }
        for part in parts
    ]

    if request.method == 'POST':
        report_form = WorkReportForm(
            request.POST, request.FILES, instance=report, require_signature=_requires_signature(task),
        )
        existing_formset = ExistingAssetFormSet(
            request.POST, initial=existing_initial, prefix='existing', form_kwargs={'site': task.site},
        )
        new_formset = NewAssetFormSet(request.POST, prefix='new')
        part_formset = PartFormSet(request.POST, prefix='parts')

        if (
            report_form.is_valid() and existing_formset.is_valid()
            and new_formset.is_valid() and part_formset.is_valid()
        ):
            with transaction.atomic():
                saved_report = save_work_report(request, task, technician, report_form, part_formset.cleaned_data)

                TaskAsset.objects.filter(task=task).delete()
                for cleaned in existing_formset.cleaned_data:
                    if cleaned.get('asset') and cleaned.get('outcome'):
                        TaskAsset.objects.create(task=task, asset=cleaned['asset'], outcome=cleaned['outcome'])
                for cleaned in new_formset.cleaned_data:
                    if cleaned.get('brand') and cleaned.get('model_name') and cleaned.get('outcome'):
                        new_asset = Asset.objects.create(
                            site=task.site, brand=cleaned['brand'], model_name=cleaned['model_name'],
                            serial_no=cleaned.get('serial_no', ''),
                        )
                        TaskAsset.objects.create(task=task, asset=new_asset, outcome=cleaned['outcome'])

            messages.success(request, report_saved_message(saved_report.first_submission, task))
            return redirect(back_url)
    else:
        report_form = WorkReportForm(instance=report, require_signature=_requires_signature(task))
        existing_formset = ExistingAssetFormSet(
            initial=existing_initial, prefix='existing', form_kwargs={'site': task.site},
        )
        new_formset = NewAssetFormSet(prefix='new')
        part_formset = PartFormSet(initial=part_initial, prefix='parts')

    context = {
        'task': task,
        'report': report,
        'back_url': back_url,
        'can_edit': True,
        'report_form': report_form,
        'existing_formset': existing_formset,
        'has_existing_assets': bool(existing_assets),
        'new_formset': new_formset,
        'part_formset': part_formset,
    }
    return render(request, 'tasks/my_report_form.html', context)
