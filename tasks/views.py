import csv
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from email.mime.image import MIMEImage

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles.finders import find as find_static_file
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import (
    Case, Count, Exists, IntegerField, Max, Min, OuterRef, Prefetch, ProtectedError, Q, Sum, Value, When,
)
from django.forms import formset_factory
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import formats, timezone, translation
from django.utils.crypto import get_random_string, salted_hmac
from django.utils.dateparse import parse_date, parse_time
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from rest_framework.authtoken.models import Token

from customers.accounts import log_account_action
from customers.models import Asset, Customer, Site
from people.models import (
    RELIABLE_LEVEL, NotificationSettings, PushDevice, RolePermission, Technician, TechnicianConduct,
    TechnicianConductAssessment, TechnicianSkill, TechnicianSkillAssessment, TechnicianTrip,
)
from people.permissions import (
    ACTIVE_COUNTRY_SESSION_KEY, can_manage_account, get_active_country, is_last_active_admin, require_admin,
    require_manager, require_permission,
    require_technician, scoped_or_404 as _scoped_or_404,
)
from people.push import push_assigned, push_cancelled, push_in_their_language, push_removed
from reference.models import Brand, ConductArea, Country, Part, Skill, TaskType
from reports.forms import PartUsedItemForm, WorkReportForm
from reports.models import CustomerFeedback, PartUsed, WorkReport

from .forms import (
    AddHelperForm, AssignTicketForm, ChangePasswordForm, NewPasswordForm, InternalTicketForm, InternalTicketNoteForm, ReassignInternalTicketForm,
    StaffTicketForm, BlockTaskForm, BrandCreateForm, CancelTaskForm, CloseTaskForm, CloseTicketForm, ConductAreaCreateForm,
    CorrectEventTimeForm, CountryCreateForm, DecideEscalationForm, EscalateTicketForm,
    CreateTechnicianLoginForm, DeactivateTechnicianForm, DismissTicketForm, ExistingAssetOutcomeForm, MarkUnavailableForm, MyProfileForm,
    NegligenceFlagForm, NewAssetForm, PartForm, PartImportForm, PauseTaskForm, RemoveAssignmentForm, ReviewLevelForm, SelfRateLevelForm, SelfRateSkillForm, SetLeadForm,
    SKILL_LEVEL_RUBRIC, SKILL_LEVEL_SHORT, NotificationSettingsForm,
    SkillCreateForm, StaffAttachmentUploadForm, TaskAttachmentUploadForm, TaskCreateForm, TaskEditForm,
    TaskMessageForm, TaskProductForm, TechnicianCreateForm, TechnicianFirstLoginForm,
    TechnicianEditForm, TechnicianTripForm, TicketEditForm, TicketInternalNoteForm, TicketLogisticsForm,
    TicketReplyForm,
)
from .location import record_tap
from .models import (
    CustomerTicket, CustomerTicketAttachment, ScheduleChangeRequest, ShippingCompany, Task, TaskAsset,
    TaskAssignment, TaskAttachment, TaskEvent, TaskMessage, TaskMessageRecipient, TaskNotification,
    TaskProduct, TicketEscalation, TicketInternalNote, TicketMachine, TicketNotification, TicketReply,
)

logger = logging.getLogger(__name__)

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

# While a task is underway, the team can still change in two cases: a
# manager or admin reshuffling it, and anyone replacing someone who has
# left (deactivated) — otherwise the task would be stuck with nobody able
# to finish it. Either way the new lead carries on; status stays
# in_progress. Once the report is filed, there's nothing left to hand over.
UNDERWAY_CHANGEABLE_STATUSES = {Task.Status.IN_PROGRESS}

# Statuses where a deactivated person's assignment still needs handing on.
NEEDS_REASSIGNING_STATUSES = [
    Task.Status.NEW, Task.Status.ASSIGNED, Task.Status.ACCEPTED, Task.Status.IN_PROGRESS, Task.Status.BLOCKED,
]


def assignment_changeable(task, assignment=None, technician=None):
    """Whether this assignment (or, with none, the team in general) can
    still change for the requesting `technician`: always before work
    starts, and while the task is still underway either for the manager
    tier, or for someone who has left.
    """
    if task.status not in ASSIGNMENT_LOCKED_STATUSES:
        return True
    if task.status not in UNDERWAY_CHANGEABLE_STATUSES:
        return False
    if technician is not None and technician.is_manager_tier:
        return True
    return bool(assignment and not assignment.technician.is_active)


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
# A manager can cancel a task any time before its report is filed — after
# that the work happened, so "Close task" is the way out instead.
CANCELLABLE_STATUSES = {
    Task.Status.NEW, Task.Status.ASSIGNED, Task.Status.ACCEPTED, Task.Status.IN_PROGRESS, Task.Status.BLOCKED,
}
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


def ticket_machines_text(machines):
    """(serial_numbers, description) text written from ticket machines:
    what staff emails, ticket lists, the mobile app and a task's
    reported_serial_numbers read. In English, like everything else written
    to a ticket for staff. Public: customers.views writes a portal ticket's
    text with it, and task_create a split task's.
    """
    serials, described = [], []
    with translation.override('en'):
        for machine in machines:
            serial = machine.serial
            if not serial and machine.serial_photo:
                serial = _('serial number on the sticker photo')
            if serial:
                serials.append(serial)
            entry = f"{machine.machine_name}{f' ({serial})' if serial else ''}: {machine.get_problem_display()}"
            if machine.out_of_service:
                entry += f" — {_('cannot be used')}"
            if machine.description:
                entry += f"\n  {machine.description}"
            described.append(entry)
    return '\n'.join(serials), '\n'.join(described)


def settle_ticket_machines(ticket, user):
    """Once every machine on a portal ticket has a decision, the ticket
    leaves the review queue: converted if any machine went to a task,
    otherwise closed. Until then it stays new, so what's left is visible.
    """
    machines = list(ticket.machines.all())
    if not machines or any(not machine.decision for machine in machines):
        return
    first_task = next((machine.task for machine in machines if machine.task_id), None)
    ticket.reviewed_by = user
    ticket.reviewed_at = timezone.now()
    if first_task is not None:
        ticket.task = ticket.task or first_task
        ticket.status = CustomerTicket.Status.CONVERTED
        ticket.save(update_fields=['task', 'status', 'reviewed_by', 'reviewed_at'])
    else:
        ticket.status = CustomerTicket.Status.CLOSED
        with translation.override('en'):
            ticket.close_reason = _('Every machine was resolved without a visit.')
        ticket.save(update_fields=['status', 'close_reason', 'reviewed_by', 'reviewed_at'])


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


def _task_work_date(task):
    """The day the work happens, in the task's own country — its scheduled
    day if it has one, otherwise today.
    """
    tz = ZoneInfo(task.site.customer.country.timezone)
    if task.scheduled_for:
        return task.scheduled_for.astimezone(tz).date()
    if task.scheduled_date:
        return task.scheduled_date
    return timezone.now().astimezone(tz).date()


def _assignment_candidates(task, exclude_ids):
    """Active technicians in the task's country, plus anyone on a trip
    there covering the task's work day (docs: technician_trip), not
    already on the task. A visitor's `country` is still their home one —
    templates show "Visiting from ..." when it differs from the task's.
    """
    country = task.site.customer.country
    work_date = _task_work_date(task)
    visiting = TechnicianTrip.objects.filter(
        country=country, start_date__lte=work_date, end_date__gte=work_date,
    ).values('technician_id')
    return Technician.objects.filter(
        Q(country=country) | Q(pk__in=visiting), is_active=True,
    ).exclude(pk__in=exclude_ids).select_related('country').order_by('full_name')


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


# A job with no estimated hours still takes up time: an hour, for clashes.
DEFAULT_JOB_HOURS = 1


def _job_window(task):
    """(start, finish) of a timed job — finish is the estimate, else an
    hour on; None for a job with no time (a date only, or no date)."""
    if task.scheduled_for is None:
        return None
    return task.scheduled_for, task.estimated_finish or task.scheduled_for + timedelta(hours=DEFAULT_JOB_HOURS)


def _with_day_load(technicians, task):
    """Annotate each technician with their other jobs on `task`'s day — no
    limit, just so a supervisor sees how busy each person is:
    `day_jobs` (their tasks that day, this one excluded, never cancelled),
    `day_hours` (the estimates that are known) and `clashes` (those whose
    time overlaps this task's). Nothing when the task has no day yet.
    """
    technicians = list(technicians)
    day = _calendar_day(task)
    for technician in technicians:
        technician.day_jobs, technician.day_hours, technician.clashes = [], 0, []
    if day is None:
        return technicians
    by_technician = {technician.pk: technician for technician in technicians}
    assignments = TaskAssignment.objects.filter(
        technician__in=by_technician, is_active=True,
    ).exclude(task=task).exclude(task__status=Task.Status.CANCELLED).filter(
        Q(task__scheduled_for__date=day) | Q(task__scheduled_for__isnull=True, task__scheduled_date=day),
    ).select_related('task')
    window = _job_window(task)
    for assignment in assignments:
        technician, job = by_technician[assignment.technician_id], assignment.task
        technician.day_jobs.append(job)
        technician.day_hours += job.estimated_hours or 0
        other = _job_window(job)
        if window and other and other[0] < window[1] and window[0] < other[1]:
            technician.clashes.append(job)
    for technician in technicians:
        technician.day_jobs.sort(key=_time_of_day)
        technician.clashes.sort(key=_time_of_day)
    return technicians


def _clash_warning(technician, task):
    """'X already has Y from 9:00 to 11:00 that day.' — for a message after
    assigning or moving a task; None without a clash."""
    clashes = _with_day_load([technician], task)[0].clashes
    if not clashes:
        return None
    return _('%(name)s already has %(jobs)s that day.') % {
        'name': technician.full_name,
        'jobs': ', '.join(
            _('%(number)s from %(start)s to %(end)s') % {
                'number': job.task_number,
                'start': formats.time_format(timezone.localtime(_job_window(job)[0])),
                'end': formats.time_format(timezone.localtime(_job_window(job)[1])),
            }
            for job in clashes
        ),
    }


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


# The mobile app's search box (api/views.py): this many newest matches.
SEARCH_RESULT_LIMIT = 50


def search_tasks(tasks, text, limit=SEARCH_RESULT_LIMIT):
    """The `limit` newest of `tasks` matching `text` anywhere in their
    number, PAK reference, site name or address, customer name, or a
    machine/product serial number. Each of those is searched on its own
    (each with its index, spots/indexes.py) for its own newest `limit`,
    and the newest of all those win — the same answer as one big OR, but
    without Postgres having to read every task to get it.
    """
    sites = Site.objects.filter(Q(name__icontains=text) | Q(address__icontains=text)).values('pk')
    customers = Customer.objects.filter(name__icontains=text).values('pk')
    by_asset = TaskAsset.objects.filter(
        asset__in=Asset.objects.filter(serial_no__icontains=text).values('pk'),
    ).values('task_id')
    by_product = TaskProduct.objects.filter(serial_number__icontains=text).values('task_id')
    ids = set()
    for condition in (
        Q(task_number__icontains=text), Q(pak_reference_number__icontains=text),
        Q(site__in=sites), Q(site__customer__in=customers), Q(pk__in=by_asset), Q(pk__in=by_product),
    ):
        ids.update(tasks.filter(condition).order_by('-reported_at').values_list('pk', flat=True)[:limit])
    return tasks.filter(pk__in=ids).order_by('-reported_at')[:limit]


def search_tickets(tickets, text):
    """Tickets matching `text` in anything the office searches them by —
    number, gym, site, address, contact name or phone, PAK reference."""
    return tickets.filter(
        Q(ticket_number__icontains=text) | Q(company_name__icontains=text)
        | Q(site_description__icontains=text) | Q(site_address__icontains=text)
        | Q(contact_name__icontains=text) | Q(contact_phone__icontains=text)
        | Q(pak_reference_number__icontains=text),
    )


def _attach_lead_technician(tasks):
    """Set `.lead_technician` on each task from the `_with_lead_prefetch` prefetch."""
    for task in tasks:
        leads = task.lead_assignments
        task.lead_technician = leads[0].technician if leads else None




def _week_start(day, country=None):
    """The first day of the week `day` is in: the country's first working
    day (Country.first_workday — Saturday in Egypt and Saudi Arabia), or
    Monday with no country (the all-countries calendar).
    """
    first = country.first_workday if country else 0
    return day - timedelta(days=(day.weekday() - first) % 7)


def _week_window(request, country=None):
    """The (today, start, end) of the 7-day window holding ?start=, or this
    week; it always starts on the country's first working day."""
    today = timezone.localtime().date()
    start = _week_start(parse_date(request.GET.get('start', '') or '') or today, country)
    end = start + timedelta(days=WEEK_LENGTH - 1)
    return today, start, end


def _week_nav_context(today, start, country=None):
    return {
        'today': today,
        'start': start,
        'prev_start': start - timedelta(days=WEEK_LENGTH),
        'next_start': start + timedelta(days=WEEK_LENGTH),
        'this_week_start': _week_start(today, country),
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
    for recipient in recipients:
        push_in_their_language(
            recipient,
            lambda: _('New message — %(number)s') % {'number': task.task_number},
            lambda: text[:150],
            {'type': 'task_message', 'task_id': task.pk},
        )
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
    for everyone else. A failed send never blocks the action that
    triggered it, but it's logged as an error, so it reaches Sentry
    instead of vanishing.

    attachment, when given, is a real file on the ticket/task itself
    (e.g. a quotation) — attached as-is, not just linked — or a
    (filename, bytes, mimetype) tuple for a generated one (the visit
    report PDF). Returns whether the email went out.

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
    if isinstance(attachment, tuple):
        email.attach(*attachment)
    elif attachment:
        attachment.open('rb')
        try:
            email.attach(attachment.name.rsplit('/', 1)[-1], attachment.read())
        finally:
            attachment.close()
    try:
        email.send()
    except Exception:  # noqa: BLE001 — never block the action; but log it, so it reaches Sentry
        logger.exception('Notification email failed: %s', subject)
        return False
    return True


def send_visit_report_copy(report, email_address):
    """Email the customer the visit report PDF (reports.pdf), and record
    where and when on the report. Both the PDF and its email are in the
    report's language (reports.pdf.report_language — Arabic in Saudi
    Arabia and Egypt), unlike other customer emails, which stay English.
    Best-effort: a failure is logged, never raised, and the PDF stays
    downloadable from the task page.
    """
    from reports.pdf import render_visit_report_pdf, report_language

    task = report.task
    customer = task.site.customer
    language = report_language(customer)
    try:
        pdf = render_visit_report_pdf(report, language)
    except Exception:  # noqa: BLE001 — same as a failed send: log it, don't break the report
        logger.exception('Visit report PDF failed: %s', task.task_number)
        return False
    contact = task.site.contact_name or customer.contact_name or customer.name
    with translation.override(language):
        context = {
            'contact': contact, 'site': task.site.name, 'number': task.task_number,
            'date': formats.date_format(
                timezone.localtime(report.submitted_at, ZoneInfo(customer.country.timezone)).date(), 'j F Y',
            ),
            'email_language': language,
        }
        subject = _('Visit report — %(number)s — Profit Sports Solutions') % context
        message = _(
            'Dear %(contact)s,\n\n'
            'Please find attached the visit report for %(site)s, dated %(date)s.\n\n'
            'Should you have any questions about the work carried out, please do not hesitate to contact us.\n\n'
            'Best regards,\n'
            'Profit Sports Solutions\n',
        ) % context
        sent = _send_notification_email(
            subject, message, 'tasks/email/visit_report.html', context, [email_address],
            attachment=(f'{task.task_number}-visit-report.pdf', pdf, 'application/pdf'),
        )
    if sent:
        report.copy_sent_to = email_address
        report.copy_sent_at = timezone.now()
        report.save(update_fields=['copy_sent_to', 'copy_sent_at'])
    return sent


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
            'We are writing to confirm that our technician is scheduled to visit %(site)s on %(date)s at '
            '%(time)s.\n\n'
            'Should this time be inconvenient, please contact us to arrange an alternative.\n\n'
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
        subject = _('Notice of a brief delay to your Profit Sports Solutions visit — %(number)s') % {
            'number': task.task_number,
        }
        message = _(
            'Dear %(contact)s,\n\n'
            'Please be advised that our technician for %(site)s is running behind schedule today: '
            '%(reason)s\n\n'
            'We sincerely apologise for any inconvenience and will arrive as soon as possible.\n\n'
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
                lines.append(_('Track your shipment here: %(url)s') % {'url': tracking_url})
        tracking_lines = '\n'.join(lines)
        message = _(
            'Dear %(contact)s,\n\n'
            'We are pleased to inform you that your shipment for %(entity)s has been dispatched. You may track '
            'it using the details below.\n\n'
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
        subject = _('Your shipment is on its way — %(task_number)s') % {'task_number': task.task_number}
        if task.shipping_company:
            tracking_line = _('Carrier: %(company)s — tracking number %(tracking)s') % {
                'company': carrier_label, 'tracking': task.shipping_tracking_number,
            }
        else:
            tracking_line = _('Tracking number: %(tracking)s') % {'tracking': task.shipping_tracking_number}
        message = _(
            'A tracking number has been assigned to the shipment for %(task_number)s (%(site)s).\n\n'
            '%(tracking_line)s\n\n'
            'View the task:\n'
            '%(link)s\n',
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
        subject = _('We would value your feedback on your recent Profit Sports Solutions visit')
        message = _(
            'Dear %(contact)s,\n\n'
            'Thank you for choosing Profit Sports Solutions. We completed a service visit at %(site)s on '
            '%(date)s, and would greatly appreciate a moment of your time to share your feedback.\n\n'
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


# Open tasks shown on the dashboard before "See all".
DASHBOARD_TASK_LIMIT = 10
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
    two lists of datetimes: what came in and what was finished. Bar
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


def _tasks_chart(country, period):
    """Tasks reported vs tasks whose first work report was submitted, per period."""
    since = _period_starts(period)[0]
    opened = Task.objects.filter(
        site__customer__country=country, reported_at__date__gte=since,
    ).values_list('reported_at', flat=True)
    completed = TaskEvent.objects.filter(
        event_type=TaskEvent.EventType.REPORT_SUBMITTED, task__site__customer__country=country,
    ).values('task_id').annotate(first=Min('occurred_at')).filter(first__date__gte=since).values_list('first', flat=True)
    return _period_chart(opened, completed, period)


def _my_trend_chart(technician):
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
    mine = TaskAssignment.objects.filter(technician=technician).values('task_id')
    completed = TaskEvent.objects.filter(
        event_type=TaskEvent.EventType.REPORT_SUBMITTED, task_id__in=mine,
    ).values('task_id').annotate(first=Min('occurred_at')).filter(first__date__gte=since).values_list('first', flat=True)
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
        tickets.filter(submitted_at__date__gte=since).values_list('submitted_at', flat=True),
        tickets.filter(reviewed_at__date__gte=since).values_list('reviewed_at', flat=True),
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
        RolePermission.objects.filter(role=technician.role, allowed=True).values_list('permission', flat=True),
    )
    task_list_url = reverse('tasks:task_list')
    open_tasks = Task.objects.filter(status__in=OPEN_STATUSES, site__customer__country=country)
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
        if technician.is_manager_tier:
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


def dashboard_overview(active_country):
    """The dashboard's figures for a country: its technicians (with their
    open-task counts), every open task in "needs someone first" order
    (emergencies, then no lead, then no date) and the counts. The website's
    Dashboard and the app's (api.views.DashboardView).
    """
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

    for task in tasks:
        task.is_unscheduled = task.scheduled_for is None and task.scheduled_date is None
    stats = {
        'available_count': sum(1 for t in technicians if t.is_available),
        'technician_count': len(technicians),
        'open_task_count': len(tasks),
        'unassigned_count': sum(1 for t in tasks if t.lead_technician is None),
        'unscheduled_count': sum(1 for t in tasks if t.is_unscheduled),
        'emergency_count': sum(1 for t in tasks if t.priority == Task.Priority.EMERGENCY),
    }
    # What needs someone first: emergencies, then no lead, then no date.
    # sorted() is stable, so the priority/schedule order holds within each.
    tasks.sort(key=lambda t: (
        t.priority != Task.Priority.EMERGENCY, t.lead_technician is not None, not t.is_unscheduled,
    ))

    return technicians, tasks, stats


@login_required
def dashboard(request):
    """At a glance: who's available today, and every open task with its
    lead and schedule. Every role but technician has view_dashboard by
    default (people migrations 0024 and 0050): a field technician has
    My schedule and My progress instead.
    """
    viewer = require_permission(request, RolePermission.Permission.VIEW_DASHBOARD)
    chart_period = 'week' if request.GET.get('period') == 'week' else 'month'
    active_country = get_active_country(request)
    technicians, tasks, stats = dashboard_overview(active_country)

    context = {
        'technicians': technicians,
        'tasks': tasks[:DASHBOARD_TASK_LIMIT],
        'stats': stats,
        'chart_period': chart_period,
        'tasks_chart': _tasks_chart(active_country, chart_period),
        # The ticket desk's own chart, only for those who handle tickets.
        'tickets_chart': (
            _tickets_chart(active_country, chart_period)
            if _has_permission(viewer, RolePermission.Permission.MANAGE_TICKETS) else None
        ),
    }
    return render(request, 'tasks/dashboard.html', context)


@login_required
def task_list(request):
    """Tasks in the active country, or — for the manager tier, with
    ?country=all — every country at once, for tracking something without
    switching countries first.
    """
    technician = require_permission(request, RolePermission.Permission.VIEW_TASKS)
    active_country = get_active_country(request)
    all_countries = technician.is_manager_tier and request.GET.get('country') == 'all'

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
    # The dashboard and home page tiles link here with one of these.
    priority = request.GET.get('priority', '')
    if priority not in Task.Priority.values:
        priority = ''
    unassigned = request.GET.get('lead') == 'none'
    unscheduled = request.GET.get('scheduled') == 'none'
    supervisor_id = request.GET.get('supervisor', '')
    if not supervisor_id.isdigit():
        supervisor_id = ''

    tasks = _with_lead_prefetch(Task.objects.select_related('site__customer__country', 'task_type'))
    customers = Customer.objects.filter(is_active=True)
    technicians = Technician.objects.filter(is_active=True)
    if not all_countries:
        tasks = tasks.filter(site__customer__country=active_country)
        customers = customers.filter(country=active_country)
        technicians = technicians.filter(country=active_country)

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

    if priority:
        tasks = tasks.filter(priority=priority)
    if unassigned:
        tasks = tasks.exclude(HAS_ACTIVE_LEAD)
    if unscheduled:
        tasks = tasks.filter(scheduled_for__isnull=True, scheduled_date__isnull=True)
    if supervisor_id:
        tasks = tasks.filter(responsible_supervisor_id=supervisor_id)

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
            'country': 'all' if all_countries else '',
            'priority': priority, 'lead': 'none' if unassigned else '',
            'scheduled': 'none' if unscheduled else '', 'supervisor': supervisor_id,
        }.items() if value
    }
    # Shown above the list with a link to clear them, since the filter
    # form below has no field for these.
    quick_filters = []
    if priority:
        quick_filters.append(Task.Priority(priority).label)
    if unassigned:
        quick_filters.append(_('Unassigned'))
    if unscheduled:
        quick_filters.append(_('Not scheduled'))
    if supervisor_id:
        quick_filters.append(_('Assigned to you'))

    context = {
        'page_obj': page_obj,
        'quick_filters': quick_filters,
        'quick_filter_params': {
            key: filter_params[key] for key in ('priority', 'lead', 'scheduled', 'supervisor') if key in filter_params
        },
        'status': status,
        'task_id': task_id,
        'site': site,
        'pak_reference': pak_reference,
        'serial_number': serial_number,
        'status_choices': Task.Status.choices,
        'customers': customers.order_by('name'),
        'technicians': technicians.order_by('full_name'),
        'shipping_company_choices': ShippingCompany.choices,
        'selected_customer': customer_id,
        'selected_technician': technician_id,
        'selected_shipping_company': shipping_company,
        'scheduled_from': request.GET.get('scheduled_from', ''),
        'scheduled_to': request.GET.get('scheduled_to', ''),
        'filter_qs': urlencode(filter_params),
        'can_view_all_countries': technician.is_manager_tier,
        'all_countries': all_countries,
    }
    return render(request, 'tasks/task_list.html', context)


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


def _report_changed_after_signing(task):
    """True when the task's report no longer matches what the customer signed."""
    from reports.pdf import changed_after_signing

    report = getattr(task, 'report', None)
    return report is not None and changed_after_signing(report)


@login_required
def task_report_pdf(request, pk):
    """The task's visit report as a PDF on the company letterhead, for the
    office to download or print: in the customer's language by default,
    or ?lang=en / ?lang=ar. Same access as the task page itself.
    """
    from reports.pdf import render_visit_report_pdf, report_language

    requesting_technician = require_permission(request, RolePermission.Permission.VIEW_TASKS)
    task = _scoped_or_404(
        Task.objects.select_related('site__customer__country', 'task_type', 'report'),
        pk, requesting_technician, get_active_country(request), 'site__customer__country',
    )
    report = getattr(task, 'report', None)
    if report is None:
        raise Http404
    language = request.GET.get('lang')
    if language not in dict(settings.LANGUAGES):
        language = report_language(task.site.customer)
    # ?version=signed: the frozen copy of what the customer signed.
    signed_version = request.GET.get('version') == 'signed' and bool(report.signed_content)
    response = HttpResponse(
        render_visit_report_pdf(report, language, signed_version=signed_version), content_type='application/pdf',
    )
    suffix = '-signed' if signed_version else ''
    response['Content-Disposition'] = f'inline; filename="{task.task_number}-visit-report{suffix}.pdf"'
    return response


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
            messages.error(request, _('Please set a scheduled time before notifying the customer.'))
        elif not task.site.effective_contact_email:
            messages.error(request, _('Please add a contact email address for this site before notifying the '
                                      'customer.'))
        else:
            _send_schedule_notification(task)
            task.schedule_notified_at = timezone.now()
            task.schedule_notified_by = request.user
            task.save(update_fields=['schedule_notified_at', 'schedule_notified_by'])
            messages.success(request, _('The customer has been notified of the scheduled visit.'))
        return redirect('tasks:task_detail', pk=task.pk)

    if request.method == 'POST' and request.POST.get('action') == 'set_schedule_time':
        set_time_technician = require_permission(request, RolePermission.Permission.CREATE_TASKS)
        _require_task_owner(request, task)
        if not task.scheduled_date or task.scheduled_for:
            messages.error(request, _('There is no day-only schedule awaiting a time.'))
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
            messages.error(request, _('This schedule is not locked. Please edit it directly.'))
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
                messages.success(request, _('Change requested. A manager will review it.'))
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
            messages.error(request, _('Please set a scheduled time before notifying the customer.'))
        elif not task.site.effective_contact_email:
            messages.error(request, _('Please add a contact email address for this site before notifying the '
                                      'customer.'))
        elif not reason:
            messages.error(request, _('Please state the reason for the delay before notifying the customer.'))
        else:
            _send_delay_notice(task, reason)
            TaskEvent.objects.create(
                task=task, event_type=TaskEvent.EventType.DELAY_NOTICE,
                occurred_at=timezone.now(), actor=request.user, note=reason,
            )
            messages.success(request, _('The customer has been notified of the delay.'))
        return redirect('tasks:task_detail', pk=task.pk)

    if request.method == 'POST' and request.POST.get('action') == 'notify_shipping':
        require_permission(request, RolePermission.Permission.CREATE_TASKS)
        _require_task_owner(request, task)
        if not task.shipping_tracking_number:
            messages.error(request, _('Please add a shipping tracking number before notifying the customer.'))
        elif not task.site.effective_contact_email:
            messages.error(request, _('Please add a contact email address for this site before notifying the '
                                      'customer.'))
        else:
            _send_shipping_notice(
                task.site.effective_contact_name or task.site.customer.name, task.site.name,
                task.task_number, task.shipping_company,
                task.shipping_tracking_number, task.site.effective_contact_email,
            )
            messages.success(request, _('The customer has been notified of the tracking number.'))
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
            messages.success(request, _('Approved. The task now awaits manager approval before closure.'))
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
            correct_tap_time(task, event, request.user, correct_form.cleaned_data)
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
            close_task_directly(task, request.user, close_task_form.cleaned_data['note'])
            messages.success(request, _('Task closed.'))
            return redirect('tasks:task_detail', pk=task.pk)

    cancel_task_form = CancelTaskForm(prefix='cancel')
    if request.method == 'POST' and request.POST.get('action') == 'cancel_task':
        require_manager(request)
        if task.status not in CANCELLABLE_STATUSES:
            messages.error(request, _('This task can no longer be cancelled.'))
            return redirect('tasks:task_detail', pk=task.pk)
        cancel_task_form = CancelTaskForm(request.POST, prefix='cancel')
        if cancel_task_form.is_valid():
            cancel_task(task, request.user, cancel_task_form.cleaned_data['note'])
            messages.success(request, _('Task cancelled.'))
            return redirect('tasks:task_detail', pk=task.pk)

    negligence_form = NegligenceFlagForm()
    if request.method == 'POST' and request.POST.get('action') == 'flag_negligence':
        require_manager(request)
        if task.status != Task.Status.CLOSED:
            messages.error(request, _('Only closed tasks can be flagged.'))
            return redirect('tasks:task_detail', pk=task.pk)
        negligence_form = NegligenceFlagForm(request.POST)
        if negligence_form.is_valid():
            flag_negligence(task, request.user, negligence_form.cleaned_data['note'])
            messages.success(request, _('Task flagged.'))
            return redirect('tasks:task_detail', pk=task.pk)

    if request.method == 'POST' and request.POST.get('action') == 'send_feedback_request':
        require_manager(request)
        feedback = getattr(task, 'feedback', None)
        if task.status != Task.Status.CLOSED:
            messages.error(request, _('Please close the task (submit its report) before requesting feedback.'))
        elif not task.site.effective_contact_email:
            messages.error(request, _('Please add a contact email address for this site before requesting '
                                      'feedback.'))
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
            messages.success(request, _('The feedback request has been sent.'))
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

    # The ticket's main task links back directly; another task split from
    # the same ticket finds it through its machines.
    source_ticket = getattr(task, 'ticket', None) or next(
        (machine.ticket for machine in task.ticket_machines.select_related('ticket')[:1]), None,
    )
    pending_schedule_request = task.schedule_change_requests.filter(
        status=ScheduleChangeRequest.Status.PENDING,
    ).select_related('requested_by').first()

    context = {
        'task': task,
        'active_lead': active_lead,
        'active_helpers': active_helpers,
        'events': events,
        'correctable_event_types': CORRECTABLE_EVENTS,
        'departed_on_task': task.status in NEEDS_REASSIGNING_STATUSES and task.assignments.filter(
            is_active=True, technician__is_active=False,
        ).exists(),
        'attachments': task.attachments.select_related('uploaded_by'),
        # A split ticket's task shows its own machines' photos (and any not
        # tied to a machine), not those of machines handled elsewhere.
        'ticket_attachments': (
            source_ticket.attachments.filter(Q(machine__task=task) | Q(machine__isnull=True))
            if source_ticket and task.ticket_machines.exists()
            else source_ticket.attachments.all() if source_ticket else []
        ),
        'task_assets': task.task_assets.select_related('asset'),
        'products': task.products.all(),
        'report': getattr(task, 'report', None),
        'report_changed_after_signing': _report_changed_after_signing(task),
        'feedback': getattr(task, 'feedback', None),
        'close_task_form': close_task_form,
        'cancel_task_form': cancel_task_form,
        'can_cancel': task.status in CANCELLABLE_STATUSES,
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
    # a cross-country edit (from the all-countries task list) must still scope
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
                notices.append(_('The customer has been notified automatically.'))
            if (
                'shipping_tracking_number' in form.changed_data and updated_task.shipping_tracking_number
                and updated_task.responsible_supervisor_id
                and NotificationSettings.load().shipment_to_supervisor_email
            ):
                _send_shipment_notice_to_supervisor(request, updated_task)
                notices.append(_('The supervisor has been notified of the shipment.'))
            messages.success(request, ' '.join([_('Task updated.')] + notices))
            weekend_warning = _weekend_warning(updated_task)
            if weekend_warning and {'scheduled_for', 'scheduled_date', 'priority'} & set(form.changed_data):
                messages.warning(request, weekend_warning)
            if {'scheduled_for', 'scheduled_date', 'estimated_hours'} & set(form.changed_data):
                for assignment in updated_task.assignments.filter(is_active=True).select_related('technician'):
                    clash = _clash_warning(assignment.technician, updated_task)
                    if clash:
                        messages.warning(request, clash)
            return redirect('tasks:task_detail', pk=task.pk)
    else:
        form = TaskEditForm(instance=task, country=task_country, is_manager=is_manager)
        product_formset = None if products_locked else TaskProductFormSet(initial=product_initial, prefix='products')

    context = {'task': task, 'form': form, 'product_formset': product_formset, 'products_locked': products_locked}
    return render(request, 'tasks/task_edit.html', context)


def create_task(request, form, creator, ticket=None, selected_machines=(), machine_decision=''):
    """Creates the task from a valid TaskCreateForm — with any new site,
    brand or task type it adds, the "created" event, the bell, the ticket
    it came from (and its machines) — and emails the staff. The website's
    New task and the app (api.views.TaskCreateView).
    """
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
            task.reported_serial_numbers = (
                ticket_machines_text(selected_machines)[0] if selected_machines else ticket.serial_numbers
            )
        # A manager's own schedule is locked from the moment it's
        # set — a supervisor's stays open. See task_edit for the
        # same rule applied to a reschedule.
        task.schedule_time_locked = creator.is_manager_tier and bool(task.scheduled_for)
        _save_new_task(task)
        TaskEvent.objects.create(
            task=task, event_type=TaskEvent.EventType.CREATED,
            occurred_at=timezone.now(), actor=request.user,
        )
        TaskNotification.objects.create(task=task, created_at=timezone.now())
        if selected_machines:
            TicketMachine.objects.filter(pk__in=[machine.pk for machine in selected_machines]).update(
                decision=machine_decision, task=task, decided_by=request.user, decided_at=timezone.now(),
            )
            if ticket.kind == CustomerTicket.Kind.CUSTOMER and ticket.customer_id is None:
                ticket.customer = site.customer
                ticket.save(update_fields=['customer'])
            settle_ticket_machines(ticket, request.user)
        elif ticket is not None:
            ticket.task = task
            # An internal ticket stays internal — linking a customer
            # would put it in their portal.
            if ticket.kind == CustomerTicket.Kind.CUSTOMER:
                ticket.customer = site.customer
            ticket.status = CustomerTicket.Status.CONVERTED
            ticket.reviewed_by = request.user
            ticket.reviewed_at = timezone.now()
            ticket.save(update_fields=['task', 'customer', 'status', 'reviewed_by', 'reviewed_at'])
    _send_new_task_email_to_staff(request, task)
    return task


@login_required
def task_create(request):
    requesting_technician = require_technician(request)
    can_create = _has_permission(requesting_technician, RolePermission.Permission.CREATE_TASKS)
    can_manage_tickets = _has_permission(requesting_technician, RolePermission.Permission.MANAGE_TICKETS)
    all_countries = can_manage_tickets and _has_permission(
        requesting_technician, RolePermission.Permission.TICKETS_ALL_COUNTRIES,
    )
    country = get_active_country(request)

    ticket = None
    ticket_id = request.GET.get('ticket')
    # Converting a ticket doesn't need create_tasks of its own: the ticket
    # desk converts customer tickets, and every manager converts internal
    # ones.
    if not (can_create or (ticket_id and (can_manage_tickets or requesting_technician.is_any_manager))):
        raise PermissionDenied
    if ticket_id:
        open_tickets = CustomerTicket.objects.filter(status=CustomerTicket.Status.NEW)
        customer_tickets = open_tickets.filter(kind=CustomerTicket.Kind.CUSTOMER)
        if not (can_manage_tickets or can_create):
            customer_tickets = customer_tickets.none()
        elif not all_countries:
            customer_tickets = customer_tickets.filter(country=country)
        internal = open_tickets.filter(kind__in=INTERNAL_TICKET_KINDS, country=country)
        if not requesting_technician.is_any_manager:
            internal = internal.none()
        ticket = get_object_or_404(customer_tickets | internal, pk=ticket_id)
        # Sites, brands and technicians all come from the ticket's own
        # country, so whoever handles every country's tickets can convert
        # one without switching to it first.
        country = ticket.country

    is_admin = requesting_technician.role == Technician.Role.ADMIN

    # A portal ticket lists its machines: this task covers just the ones
    # the manager selected (?machines=1,3), the others stay for another
    # task or another decision (see ticket_review).
    selected_machines, machine_decision = [], ''
    if ticket is not None and ticket.machines.exists():
        machine_ids = [int(value) for value in request.GET.get('machines', '').split(',') if value.isdigit()]
        selected_machines = list(ticket.machines.filter(pk__in=machine_ids, decision=''))
        if not selected_machines:
            messages.error(request, _('Please select at least one machine that has not been decided yet.'))
            return redirect('tasks:ticket_review', pk=ticket.pk)
        machine_decision = request.GET.get('decision')
        if machine_decision not in TicketMachine.TASK_DECISIONS:
            machine_decision = TicketMachine.Decision.VISIT

    if request.method == 'POST':
        form = TaskCreateForm(request.POST, country=country, ticket=ticket, is_admin=is_admin)
        if form.is_valid():
            task = create_task(request, form, requesting_technician, ticket, selected_machines, machine_decision)
            messages.success(request, _('Task %(number)s created.') % {'number': task.task_number})
            weekend_warning = _weekend_warning(task)
            if weekend_warning:
                messages.warning(request, weekend_warning)
            return redirect('tasks:task_detail', pk=task.pk)
    else:
        form = TaskCreateForm(country=country, ticket=ticket, is_admin=is_admin)
        if selected_machines:
            form.fields['description'].initial = ticket_machines_text(selected_machines)[1]

    # So whoever's creating this can see everyone's upcoming schedule
    # right here and pick a date that doesn't collide — no separate trip
    # to the week board just to check.
    team_schedule = _supervisors_with_next_scheduled_task(
        Technician.objects.filter(is_active=True, country=country).exclude(role=Technician.Role.TECHNICIAN),
    ) + _technicians_with_next_scheduled_task(
        Technician.objects.filter(is_active=True, country=country, role=Technician.Role.TECHNICIAN),
    )
    # Everyone's next booking shows; only those the creator oversees link to their schedule.
    _mark_openable(team_schedule, requesting_technician)

    return render(request, 'tasks/task_create.html', {
        'form': form, 'ticket': ticket, 'team_schedule': team_schedule,
        'selected_machines': selected_machines, 'machine_decision': machine_decision,
    })


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
    if not NotificationSettings.load().ticket_received_email:  # the admin's Notifications page
        return
    if not ticket.contact_email:
        return
    link = request.build_absolute_uri(reverse('tasks:ticket_status', args=[ticket.token]))
    with translation.override('en'):
        subject = _('Ticket %(number)s received — Profit Sports Solutions') % {'number': ticket.ticket_number}
        message = _(
            'Dear %(contact)s,\n\n'
            'Thank you for contacting us. We have received your request and created ticket #%(number)s.\n\n'
            'Our team responds within 24 hours, Sunday to Friday. You will receive an email as soon as there '
            'is an update.\n\n'
            'Please ensure that photos or videos are attached for each issue. This will help our team '
            'understand the problem and resolve your case promptly.\n\n'
            'Best regards,\n'
            'Profit Sports Solutions\n',
        ) % {'contact': ticket.contact_name, 'number': ticket.ticket_number}
    _send_notification_email(
        subject, message, 'tasks/email/ticket_confirmation.html',
        {'contact': ticket.contact_name, 'ticket_number': ticket.ticket_number, 'link': link},
        [ticket.contact_email],
    )


# Staff setup links: the same kind of signed, one-time link as the
# customer portal's (customers.accounts), with their own salt so a link
# made for one can never open the other.
STAFF_LINK_SALT = 'tasks.staff-setup-link'
STAFF_LINK_VALID_DAYS = 7


def _staff_link_fingerprint(user):
    # Changes the moment the password does, so each link works only once.
    return salted_hmac(STAFF_LINK_SALT, f'{user.pk}:{user.password}').hexdigest()[:20]


def make_staff_link_token(user):
    return signing.dumps({'u': user.pk, 'f': _staff_link_fingerprint(user)}, salt=STAFF_LINK_SALT)


def staff_user_for_link_token(token):
    """The staff login a setup link belongs to, or None if it's tampered
    with, older than STAFF_LINK_VALID_DAYS, already used, or the login or
    the person is no longer active."""
    try:
        data = signing.loads(token, salt=STAFF_LINK_SALT, max_age=timedelta(days=STAFF_LINK_VALID_DAYS))
    except signing.BadSignature:
        return None
    user = get_user_model().objects.filter(
        pk=data.get('u'), is_active=True, technician__is_active=True,
    ).select_related('technician').first()
    if user is None or data.get('f') != _staff_link_fingerprint(user):
        return None
    return user


def send_staff_setup_link(request, technician):
    """Emails a one-time link to set a password: for a new login, or for
    anyone who can't sign in. No password ever goes by email. False if
    there's no email address to send it to.
    """
    user = technician.user
    if not user.email:
        return False
    link = request.build_absolute_uri(reverse('tasks:staff_setup', args=[make_staff_link_token(user)]))
    context = {'contact': technician.full_name, 'username': user.get_username(), 'link': link,
               'days': STAFF_LINK_VALID_DAYS}
    with translation.override('en'):
        subject = _('Set up your Profit Sports Solutions account')
        message = _(
            'Dear %(contact)s,\n\n'
            'Your Profit Sports Solutions account has been created. Please set your password using the secure '
            'link below. The link is valid for %(days)s days and can be used once.\n\n'
            '%(link)s\n\n'
            'Your username: %(username)s\n\n'
            'If the link has expired, please ask your manager to send a new one.\n\n'
            'Best regards,\n'
            'Profit Sports Solutions\n'
        ) % context
    _send_notification_email(subject, message, 'tasks/email/staff_setup_link.html', context, [user.email])
    technician.setup_link_sent_at = timezone.now()
    technician.save(update_fields=['setup_link_sent_at'])
    return True


def _send_reply_to_customer(request, reply):
    """Best-effort, and only when the customer gave an email — same
    fail-silent pattern as every other customer-facing notification.
    Any file attached to the reply (quotation, report, photo, whatever
    staff chose to include) rides along on the email itself, not just
    linked from the portal.
    """
    if not NotificationSettings.load().reply_to_customer_email:  # the admin's Notifications page
        return
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
            'You may reply or view the full conversation here:\n'
            '%(link)s\n\n'
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
    if not NotificationSettings.load().reply_to_staff_email:  # the admin's Notifications page
        return
    ticket = reply.ticket
    assignee = ticket.assigned_to
    if not assignee or not assignee.user_id or not assignee.user.email:
        return
    link = request.build_absolute_uri(reverse('tasks:ticket_review', args=[ticket.pk]))
    with translation.override('en'):
        subject = _('New customer reply — ticket %(number)s') % {'number': ticket.ticket_number}
        message = _(
            'The customer has replied to ticket %(number)s (%(company)s — %(site)s):\n\n'
            '%(reply_message)s\n\n'
            'View and reply:\n'
            '%(link)s\n',
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


def _send_escalation_email(request, escalation):
    """Best-effort email to whoever a ticket was escalated to."""
    if not NotificationSettings.load().escalation_emails:  # the admin's Notifications page
        return
    recipient = escalation.escalated_to
    if not recipient.user_id or not recipient.user.email:
        return
    ticket = escalation.ticket
    link = request.build_absolute_uri(reverse('tasks:ticket_review', args=[ticket.pk]))
    with translation.override('en'):
        subject = _('Ticket escalated to you — %(number)s') % {'number': ticket.ticket_number}
        message = _(
            '%(by)s has escalated ticket %(number)s (%(company)s — %(site)s) to you for a decision:\n\n'
            '%(reason)s\n\n'
            'Please record your decision here:\n'
            '%(link)s\n',
        ) % {
            'by': escalation.escalated_by.full_name, 'number': ticket.ticket_number,
            'company': ticket.company_name, 'site': ticket.site_description, 'reason': escalation.reason,
            'link': link,
        }
    _send_notification_email(
        subject, message, 'tasks/email/ticket_escalated.html',
        {
            'escalated_by': escalation.escalated_by.full_name, 'ticket_number': ticket.ticket_number,
            'company': ticket.company_name, 'site': ticket.site_description, 'reason': escalation.reason,
            'link': link,
        },
        [recipient.user.email],
    )


def _send_escalation_decided_email(request, escalation):
    """Best-effort email back to whoever escalated, once it's decided."""
    if not NotificationSettings.load().escalation_emails:  # the admin's Notifications page
        return
    recipient = escalation.escalated_by
    if not recipient.user_id or not recipient.user.email:
        return
    ticket = escalation.ticket
    link = request.build_absolute_uri(reverse('tasks:ticket_review', args=[ticket.pk]))
    with translation.override('en'):
        subject = _('Escalation decided — %(number)s') % {'number': ticket.ticket_number}
        decision = escalation.get_decision_display()
        message = _(
            '%(by)s has issued a decision on the escalation of ticket %(number)s: %(decision)s.\n\n'
            '%(note)s\n\n'
            'Please continue processing the ticket here:\n'
            '%(link)s\n',
        ) % {
            'by': escalation.escalated_to.full_name, 'number': ticket.ticket_number, 'decision': decision,
            'note': escalation.decision_note, 'link': link,
        }
    _send_notification_email(
        subject, message, 'tasks/email/escalation_decided.html',
        {
            'decided_by': escalation.escalated_to.full_name, 'ticket_number': ticket.ticket_number,
            'decision': decision, 'note': escalation.decision_note, 'link': link,
        },
        [recipient.user.email],
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


def send_new_ticket_email_to_staff(request, ticket):
    """Best-effort, same fail-silent pattern as every other notification
    here — the roles chosen on the admin's Notifications page (by default
    whoever can manage_tickets), in the ticket's country, alongside the bell (people.context_processors.notification_
    bell). Public (no leading underscore): customers.views.portal_ticket_
    new calls this directly, the same way it already calls save_new_ticket.
    """
    switches = NotificationSettings.load()
    if not switches.new_ticket_email:
        return
    recipients = _staff_notification_recipients(ticket.country, switches.new_ticket_roles)
    if not recipients:
        return
    link = request.build_absolute_uri(reverse('tasks:ticket_review', args=[ticket.pk]))
    with translation.override('en'):
        subject = _('New ticket %(number)s — %(company)s') % {
            'number': ticket.ticket_number, 'company': ticket.company_name,
        }
        message = _(
            'A new ticket has been received.\n\n'
            'Ticket: %(number)s\n'
            'Company: %(company)s\n'
            'Site: %(site)s\n'
            'Description: %(description)s\n\n'
            'Review the ticket:\n'
            '%(link)s\n',
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
    here — the roles chosen on the admin's Notifications page (by default
    managers and admins), in the task's country, alongside the bell.
    """
    switches = NotificationSettings.load()
    if not switches.new_task_email:
        return
    recipients = _staff_notification_recipients(task.site.customer.country, switches.new_task_roles)
    if not recipients:
        return
    link = request.build_absolute_uri(reverse('tasks:task_detail', args=[task.pk]))
    with translation.override('en'):
        subject = _('New task %(number)s — %(site)s') % {'number': task.task_number, 'site': task.site.name}
        message = _(
            'A new task has been created.\n\n'
            'Task: %(number)s\n'
            'Site: %(site)s\n'
            'Description: %(description)s\n\n'
            'View the task:\n'
            '%(link)s\n',
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
        # Only what the customer may see is used from these: the dates and
        # customer_message — never reason, decision or decision_note.
        'escalations': ticket.escalations.order_by('escalated_at'),
    }
    return render(request, 'tasks/ticket_status.html', context)


@login_required
def ticket_list(request):
    """Every customer-submitted ticket still needing a decision, plus
    what's already been resolved — country-scoped like everything else a
    supervisor reviews, or every country at once with ?country=all. A
    separate, independent field for each thing you'd actually search a
    ticket by, instead of one combined box. Opening, converting,
    dismissing or assigning a ticket from another country works straight
    from here — task_create builds around the ticket's own country.
    """
    technician = require_permission(request, RolePermission.Permission.MANAGE_TICKETS)
    can_view_all_countries = _has_permission(technician, RolePermission.Permission.TICKETS_ALL_COUNTRIES)
    all_countries = can_view_all_countries and request.GET.get('country') == 'all'

    status = request.GET.get('status', 'new')
    ticket_id = request.GET.get('ticket_id', '').strip()
    company = request.GET.get('company', '').strip()
    site = request.GET.get('site', '').strip()
    pak = request.GET.get('pak', '').strip()
    customer_id = request.GET.get('customer', '')
    date = parse_date(request.GET.get('date', '') or '')
    active_country = get_active_country(request)

    tickets = CustomerTicket.objects.filter(kind=CustomerTicket.Kind.CUSTOMER).select_related(
        'assigned_to', 'country', 'customer',
    ).annotate(
        is_escalated=Exists(
            TicketEscalation.objects.filter(ticket=OuterRef('pk'), decision=TicketEscalation.Decision.PENDING),
        ),
    )
    customers = Customer.objects.filter(is_active=True)
    if not all_countries:
        tickets = tickets.filter(country=active_country)
        customers = customers.filter(country=active_country)
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
        customer = get_object_or_404(customers, pk=customer_id)
        tickets = tickets.filter(Q(customer_id=customer_id) | Q(company_name__iexact=customer.name))
    if date:
        tickets = tickets.filter(submitted_at__date=date)
    tickets = tickets.order_by('-submitted_at')

    filter_params = {
        key: value for key, value in {
            'ticket_id': ticket_id, 'company': company, 'site': site, 'pak': pak, 'customer': customer_id,
            'date': request.GET.get('date', ''), 'country': 'all' if all_countries else '',
        }.items() if value
    }

    context = {
        'tickets': tickets, 'status': status, 'status_choices': CustomerTicket.Status.choices,
        'ticket_id': ticket_id, 'company': company, 'site': site, 'pak': pak,
        'date': request.GET.get('date', ''),
        'customers': customers.order_by('name'),
        'selected_customer': customer_id,
        'filter_qs': urlencode(filter_params),
        'can_view_all_countries': can_view_all_countries,
        'all_countries': all_countries,
    }
    return render(request, 'tasks/ticket_list.html', context)


@login_required
def ticket_review(request, pk):
    """Dismiss, assign, or convert a ticket — restricted to manage_tickets
    like the rest of ticket triage. Whoever it's currently assigned to can
    also open it read-only, even without that permission, so they can see
    what they've been asked to look into; they can't act on it themselves.
    """
    technician = require_technician(request)
    can_manage = _has_permission(technician, RolePermission.Permission.MANAGE_TICKETS)
    # Whoever handles every country's tickets (tickets_all_countries)
    # reaches any of them — the point of the all-countries ticket list — and
    # so does anyone a ticket was escalated to, wherever they're based.
    # Everyone else is country-scoped in the fetch itself, same as every
    # other cross-country lookup in this app — an assignee is always
    # same-country by construction (AssignTicketForm only offers
    # same-country technicians), so this costs it nothing.
    reaches_all = can_manage and _has_permission(technician, RolePermission.Permission.TICKETS_ALL_COUNTRIES)
    if reaches_all or TicketEscalation.objects.filter(ticket_id=pk, escalated_to=technician).exists():
        ticket = get_object_or_404(CustomerTicket, pk=pk)
    else:
        ticket = _scoped_or_404(CustomerTicket.objects, pk, technician, get_active_country(request), 'country')
    if ticket.kind != CustomerTicket.Kind.CUSTOMER:
        return redirect('tasks:internal_ticket', pk=ticket.pk)

    is_assignee = ticket.assigned_to_id == technician.id
    escalations = list(ticket.escalations.select_related('escalated_by', 'escalated_to'))
    # Whoever a ticket was escalated to can open it (read-only, like an
    # assignee) — and decide it while it's pending.
    is_escalated_to = any(e.escalated_to_id == technician.id for e in escalations)
    if not (can_manage or is_assignee or is_escalated_to):
        raise PermissionDenied
    pending_escalation = next((e for e in escalations if e.decision == TicketEscalation.Decision.PENDING), None)
    can_decide = bool(
        pending_escalation and pending_escalation.escalated_to_id == technician.id
        and RolePermission.objects.filter(
            role=technician.role, permission=RolePermission.Permission.DECIDE_ESCALATED_TICKETS, allowed=True,
        ).exists()
    )
    # Strictly one level up (Technician.escalates_to_role). Whoever holds
    # the pending escalation can pass it on to the level above them instead
    # of deciding it themselves.
    holds_pending = pending_escalation is not None and pending_escalation.escalated_to_id == technician.id
    can_escalate = (
        can_manage and _ticket_is_open(ticket) and technician.escalates_to_role is not None
        and (pending_escalation is None or holds_pending)
    )

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
    escalate_form = EscalateTicketForm(country=ticket.country, escalated_by=technician)
    decide_form = DecideEscalationForm()

    if request.method == 'POST' and request.POST.get('action') == 'decide_escalation' and can_decide:
        decide_form = DecideEscalationForm(request.POST)
        if decide_form.is_valid():
            pending_escalation.decision = decide_form.cleaned_data['decision']
            pending_escalation.decision_note = decide_form.cleaned_data['decision_note']
            pending_escalation.decided_at = timezone.now()
            pending_escalation.save(update_fields=['decision', 'decision_note', 'decided_at'])
            TicketNotification.objects.create(
                ticket=ticket, kind=TicketNotification.Kind.ESCALATION_DECIDED, created_at=timezone.now(),
            )
            _send_escalation_decided_email(request, pending_escalation)
            messages.success(request, _('The decision has been returned to the support desk.'))
            return redirect('tasks:ticket_review', pk=ticket.pk)

    if (
        request.method == 'POST' and can_manage and request.POST.get('action') == 'machines'
        and ticket.status == CustomerTicket.Status.NEW
    ):
        # The selected machines: a task for them (visit or part shipment,
        # created on the next page), or settled here without one.
        undecided = ticket.machines.filter(decision='')
        selected = list(undecided.filter(pk__in=[v for v in request.POST.getlist('machines') if v.isdigit()]))
        choice = request.POST.get('do')
        if not selected:
            messages.error(request, _('Please select at least one machine that has not been decided yet.'))
        elif choice in TicketMachine.TASK_DECISIONS:
            ids = ','.join(str(machine.pk) for machine in selected)
            return redirect(f"{reverse('tasks:task_create')}?ticket={ticket.pk}&machines={ids}&decision={choice}")
        elif choice in (TicketMachine.Decision.ADVICE, TicketMachine.Decision.NOT_COVERED):
            note = request.POST.get('decision_note', '').strip()[:255]
            TicketMachine.objects.filter(pk__in=[machine.pk for machine in selected]).update(
                decision=choice, decision_note=note, decided_by=request.user, decided_at=timezone.now(),
            )
            settle_ticket_machines(ticket, request.user)
            messages.success(request, _('Decision saved.'))
        return redirect('tasks:ticket_review', pk=ticket.pk)

    if request.method == 'POST' and can_manage:
        action = request.POST.get('action')

        if action == 'escalate' and can_escalate:
            escalate_form = EscalateTicketForm(request.POST, country=ticket.country, escalated_by=technician)
            if escalate_form.is_valid():
                now = timezone.now()
                if pending_escalation is not None:
                    # Passing it up: this step ends as forwarded, and the
                    # one who raised it hears that it went further.
                    pending_escalation.decision = TicketEscalation.Decision.FORWARDED
                    pending_escalation.decision_note = escalate_form.cleaned_data['reason']
                    pending_escalation.decided_at = now
                    pending_escalation.save(update_fields=['decision', 'decision_note', 'decided_at'])
                    TicketNotification.objects.create(
                        ticket=ticket, kind=TicketNotification.Kind.ESCALATION_DECIDED, created_at=now,
                    )
                    _send_escalation_decided_email(request, pending_escalation)
                escalation = TicketEscalation.objects.create(
                    ticket=ticket, escalated_by=technician,
                    escalated_to=escalate_form.cleaned_data['escalated_to'],
                    reason=escalate_form.cleaned_data['reason'],
                    customer_message=escalate_form.cleaned_data['customer_message'],
                    escalated_at=now,
                )
                _send_escalation_email(request, escalation)
                recipient = escalation.escalated_to
                if not (recipient.user_id and recipient.user.email):
                    messages.warning(request, _(
                        '%(name)s has no email address on record and was therefore notified in the application '
                        'only. Please add an email address to their profile.',
                    ) % {'name': recipient.full_name})
                push_in_their_language(
                    escalation.escalated_to,
                    lambda: _('Ticket escalated to you'),
                    lambda: _('%(number)s — %(company)s') % {
                        'number': ticket.ticket_number, 'company': ticket.company_name,
                    },
                    {'type': 'ticket', 'ticket_id': ticket.pk},
                )
                messages.success(request, _('Escalated to %(name)s.') % {'name': escalation.escalated_to.full_name})
                return redirect('tasks:ticket_review', pk=ticket.pk)

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
                messages.error(request, _('Please add a shipping tracking number before notifying the customer.'))
            elif not ticket.contact_email:
                messages.error(request, _('Please add a contact email address before notifying the customer.'))
            else:
                _send_shipping_notice(
                    ticket.contact_name, ticket.site_description, f'ticket #{ticket.pk}',
                    ticket.shipping_company,
                    ticket.shipping_tracking_number, ticket.contact_email,
                )
                messages.success(request, _('The customer has been notified of the tracking number.'))
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
        # Photos not tied to one of the ticket's machines (staff-opened and
        # older tickets); a machine's own show in the machines table.
        'ticket_general_attachments': ticket.attachments.filter(machine__isnull=True),
        # Every task made from it: the one for a single conversion, or each
        # group of machines on a split ticket.
        'ticket_tasks': (
            Task.objects.filter(ticket_machines__ticket=ticket).distinct().order_by('pk')
            or ([ticket.task] if ticket.task_id else [])
        ),
        'ticket': ticket, 'dismiss_form': dismiss_form, 'close_form': close_form, 'assign_form': assign_form,
        'logistics_form': logistics_form, 'edit_form': edit_form, 'can_manage': can_manage,
        'reply_form': reply_form, 'can_reply': can_reply, 'is_admin': technician.role == Technician.Role.ADMIN,
        'replies': ticket.replies.select_related('sent_by'),
        'escalations': escalations, 'pending_escalation': pending_escalation,
        'can_escalate': can_escalate, 'escalate_form': escalate_form,
        'escalation_role': Technician.Role(technician.escalates_to_role).label if technician.escalates_to_role else '',
        'can_decide': can_decide, 'decide_form': decide_form,
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


def _set_lead(task, active_lead, technician, end_reason, actor, hours_worked=None):
    """`hours_worked`: what the outgoing lead already put in, on a job
    already underway — counted toward their hours (_handover_hours)."""
    with transaction.atomic():
        if active_lead:
            active_lead.is_active = False
            active_lead.ended_at = timezone.now()
            active_lead.end_reason = end_reason
            if hours_worked is not None:
                active_lead.labour_hours = hours_worked
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
    push_assigned(technician, task, is_lead=True)
    if active_lead and active_lead.technician != technician:
        push_removed(active_lead.technician, task)


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

    locked = not assignment_changeable(task, technician=requesting_technician)
    # Work has started, but a manager/admin can still reshuffle the team.
    manager_override = not locked and task.status in ASSIGNMENT_LOCKED_STATUSES
    can_set_lead = assignment_changeable(task, active_lead, requesting_technician)
    for helper in active_helpers:
        helper.removable = assignment_changeable(task, helper, requesting_technician)
    candidates_qs = _assignment_candidates(task, exclude_ids=assigned_ids)
    # Shown in the candidates table regardless of availability, so a
    # supervisor can see and flip someone back — but only available
    # technicians can actually be picked as lead or helper.
    selectable_qs = candidates_qs.filter(is_available=True)

    # Only the manager tier records a replaced lead's hours — supervisors
    # never set anyone's working hours.
    ask_hours = (
        bool(active_lead) and task.status == Task.Status.IN_PROGRESS and requesting_technician.is_manager_tier
    )
    set_lead_form = SetLeadForm(technicians=selectable_qs, requires_reason=bool(active_lead), ask_hours=ask_hours)
    add_helper_form = AddHelperForm(technicians=selectable_qs) if active_lead else None

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'set_lead' and can_set_lead:
            set_lead_form = SetLeadForm(
                request.POST, technicians=selectable_qs, requires_reason=bool(active_lead), ask_hours=ask_hours,
            )
            if set_lead_form.is_valid():
                _set_lead(
                    task, active_lead, set_lead_form.cleaned_data['technician'],
                    set_lead_form.cleaned_data.get('end_reason', ''), request.user,
                    set_lead_form.cleaned_data.get('hours_worked'),
                )
                messages.success(request, _('Lead technician set.'))
                clash = _clash_warning(set_lead_form.cleaned_data['technician'], task)
                if clash:
                    messages.warning(request, clash)
                return redirect('tasks:task_assign', pk=task.pk)

        elif action == 'add_helper' and active_lead and not locked:
            add_helper_form = AddHelperForm(request.POST, technicians=selectable_qs)
            if add_helper_form.is_valid():
                TaskAssignment.objects.create(
                    task=task, technician=add_helper_form.cleaned_data['technician'],
                    role=TaskAssignment.Role.HELPER, assigned_at=timezone.now(), is_active=True,
                )
                push_assigned(add_helper_form.cleaned_data['technician'], task, is_lead=False)
                messages.success(request, _('Helper added.'))
                clash = _clash_warning(add_helper_form.cleaned_data['technician'], task)
                if clash:
                    messages.warning(request, clash)
                return redirect('tasks:task_assign', pk=task.pk)

        elif action == 'remove_helper':
            helper = get_object_or_404(
                TaskAssignment.objects.select_related('technician'), pk=request.POST.get('assignment_id'), task=task,
                role=TaskAssignment.Role.HELPER, is_active=True,
            )
            remove_form = RemoveAssignmentForm(request.POST)
            if assignment_changeable(task, helper, requesting_technician) and remove_form.is_valid():
                helper.is_active = False
                helper.ended_at = timezone.now()
                helper.end_reason = remove_form.cleaned_data['end_reason']
                helper.save()
                push_removed(helper.technician, task)
                messages.success(request, _('Helper removed.'))
                return redirect('tasks:task_assign', pk=task.pk)

    context = {
        'task': task,
        'active_lead': active_lead,
        'active_helpers': active_helpers,
        'candidates': _mark_openable(_with_day_load(
            _technicians_with_next_scheduled_task(_candidates_with_skill_level(candidates_qs, task)), task,
        ), requesting_technician),
        'task_day': _calendar_day(task),
        'locked': locked,
        'manager_override': manager_override,
        'can_set_lead': can_set_lead,
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
                set_availability(technician, False, mark_unavailable_form.cleaned_data['reason'])
                messages.success(request, _('Marked as unavailable.'))
                return redirect('tasks:technician_availability')

        elif action == 'mark_available':
            set_availability(technician, True)
            messages.success(request, _('Marked as available.'))
            return redirect('tasks:technician_availability')

    return render(request, 'tasks/technician_availability.html', {
        'technicians': technicians, 'mark_unavailable_form': mark_unavailable_form,
    })


# Tasks listed in one day of the month view before "+N more".
MONTH_DAY_LIMIT = 3


def _calendar_day(task):
    """The local day a task is booked for: its time if set, else its day-only date."""
    return timezone.localtime(task.scheduled_for).date() if task.scheduled_for else task.scheduled_date


def _weekend_warning(task):
    """A reminder, not a block, when a task that isn't an emergency is
    planned for one of its country's days off — the team works weekends
    for emergencies only. None otherwise.
    """
    day = _calendar_day(task)
    country = task.site.customer.country
    if day is None or task.priority == Task.Priority.EMERGENCY or day.weekday() not in country.weekend_weekdays:
        return None
    return _('%(day)s is a day off in %(country)s; weekend work is normally for emergencies only.') % {
        'day': formats.date_format(day, 'l j F'), 'country': country.display_name,
    }


def _calendar_context(request, tasks, country=None):
    """A week (default, ?start=), month (?view=month&month=YYYY-MM) or year
    (?view=year&year=YYYY) of `tasks` laid out by local day, plus the open
    tasks with no day at all. A task booked by day only (scheduled_date)
    shows on its day too, after the timed ones. Weeks start on the
    country's first working day and its days off are marked. Shared by the
    country and all-countries calendars and My schedule.
    """
    today = timezone.localdate()
    if request.GET.get('view') == 'year':
        year = request.GET.get('year', '')
        year = int(year) if year.isdigit() and 2000 <= int(year) <= 2100 else today.year
        start = _week_start(date(year, 1, 1), country)
        end = _week_start(date(year, 12, 31), country) + timedelta(days=WEEK_LENGTH - 1)
        nav = {'view': 'year', 'today': today, 'year': year, 'start': start, 'month': date(year, 1, 1)}
    elif request.GET.get('view') == 'month':
        month = parse_date(f"{request.GET.get('month', '')}-01") or today.replace(day=1)
        next_month = (month + timedelta(days=32)).replace(day=1)
        last_day = next_month - timedelta(days=1)
        start = _week_start(month, country)
        end = _week_start(last_day, country) + timedelta(days=WEEK_LENGTH - 1)
        nav = {
            'view': 'month', 'today': today, 'month': month, 'start': start,
            'prev_month': (month - timedelta(days=1)).replace(day=1), 'next_month': next_month,
            'this_month': today.replace(day=1),
        }
    else:
        today, start, end = _week_window(request, country)
        nav = {'view': 'week', 'end': end, **_week_nav_context(today, start, country)}

    booked = list(_with_lead_prefetch(
        tasks.filter(
            Q(scheduled_for__date__range=(start, end))
            | Q(scheduled_for__isnull=True, scheduled_date__range=(start, end)),
        ).select_related('site__customer__country', 'task_type'),
    ))
    _attach_lead_technician(booked)
    latest = datetime.max.time()
    booked.sort(key=lambda task: (
        _calendar_day(task),
        timezone.localtime(task.scheduled_for).time() if task.scheduled_for else latest,
        task.task_number,
    ))

    weekend = country.weekend_weekdays if country else set()
    days = []
    by_date = {}
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        entry = {
            'date': day, 'tasks': [], 'is_today': day == today, 'is_weekend': day.weekday() in weekend,
            'in_month': nav['view'] == 'week' or day.month == nav['month'].month,
            'week_start': _week_start(day, country),
        }
        days.append(entry)
        by_date[day] = entry['tasks']
    for task in booked:
        by_date[_calendar_day(task)].append(task)
    for entry in days:
        entry['shown'] = entry['tasks'][:MONTH_DAY_LIMIT]
        entry['more'] = len(entry['tasks']) - len(entry['shown'])

    unscheduled = _with_lead_prefetch(
        tasks.filter(scheduled_for__isnull=True, scheduled_date__isnull=True, status__in=OPEN_STATUSES)
        .select_related('site__customer__country', 'task_type')
        .annotate(priority_rank=PRIORITY_RANK).order_by('priority_rank', 'promised_at'),
    )
    _attach_lead_technician(unscheduled)

    context = {
        **nav,
        'days': days,
        'weeks': [days[i:i + WEEK_LENGTH] for i in range(0, len(days), WEEK_LENGTH)],
        'unscheduled': unscheduled,
    }
    if nav['view'] == 'year':
        context['year_months'] = _year_months(nav['year'], days, country)
    return context


def _year_months(year, days, country):
    """The year view's twelve small months: each its own whole weeks of
    the day entries above (a day outside the month left blank), and its
    count of visits."""
    by_date = {entry['date']: entry for entry in days}
    months = []
    for number in range(1, 13):
        first = date(year, number, 1)
        last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        start = _week_start(first, country)
        span = (_week_start(last, country) - start).days + WEEK_LENGTH
        cells = [
            by_date[start + timedelta(days=offset)] if (start + timedelta(days=offset)).month == number else None
            for offset in range(span)
        ]
        months.append({
            'first': first,
            'cells': cells,
            'count': sum(len(cell['tasks']) for cell in cells if cell),
        })
    return months


@login_required
def task_week(request):
    """The schedule calendar for the active country, by week or by month."""
    require_permission(request, RolePermission.Permission.VIEW_TASKS)
    country = get_active_country(request)
    tasks = Task.objects.filter(site__customer__country=country)
    return render(request, 'tasks/task_week.html', _calendar_context(request, tasks, country))


@login_required
def all_week(request):
    """The same calendar for every country at once, each card naming its
    country. Manager-only, same fixed floor as the other "all ..." boards.
    """
    require_manager(request)
    return render(request, 'tasks/all_week.html', {**_calendar_context(request, Task.objects.all()), 'show_country': True})


# Still to be done: what makes a past-dated task overdue (a task whose
# report is in, or waiting for review, isn't).
NOT_DONE_STATUSES = [
    Task.Status.NEW, Task.Status.ASSIGNED, Task.Status.ACCEPTED, Task.Status.IN_PROGRESS, Task.Status.BLOCKED,
]


def _my_tasks(technician):
    """(queryset, with_role) for everything on someone's own plate: tasks
    they're assigned to (lead or helper) and tasks they're the responsible
    supervisor for — once each, under its assignment — never cancelled
    ones. with_role(tasks) lists them, each with `.my_role` set.
    """
    roles = dict(
        TaskAssignment.objects.filter(technician=technician, is_active=True).values_list('task_id', 'role'),
    )
    for task_id in Task.objects.filter(responsible_supervisor=technician).exclude(pk__in=roles).values_list(
        'pk', flat=True,
    ):
        roles[task_id] = 'responsible'
    mine = Task.objects.filter(pk__in=roles).exclude(status=Task.Status.CANCELLED).select_related(
        'site__customer', 'task_type',
    )

    def with_role(tasks):
        tasks = list(tasks)
        for task in tasks:
            task.my_role = roles[task.pk]
        return tasks

    return mine, with_role


def _time_of_day(task):
    """Sort key within one day: timed tasks by time, then the day-only ones."""
    return (
        task.scheduled_for is None,
        timezone.localtime(task.scheduled_for).time() if task.scheduled_for else datetime.min.time(),
        task.task_number,
    )


def _overdue(mine, with_role, today):
    """Still to be done, planned for a day before today, oldest first."""
    return sorted(with_role(mine.filter(status__in=NOT_DONE_STATUSES).filter(
        Q(scheduled_for__date__lt=today) | Q(scheduled_for__isnull=True, scheduled_date__lt=today),
    )), key=lambda task: (_calendar_day(task), task.task_number))


def today_plan(technician):
    """My schedule's Today view: today's tasks split into the next job (one
    already under way first, else the earliest still to do), the rest
    still to do, and those done — plus overdue. The next job carries the
    lead's next tap (`next_action`) and its site's map link.
    """
    today = timezone.localtime().date()
    mine, with_role = _my_tasks(technician)
    todays = sorted(with_role(mine.filter(
        Q(scheduled_for__date=today) | Q(scheduled_for__isnull=True, scheduled_date=today),
    )), key=_time_of_day)
    to_do = [task for task in todays if task.status in NOT_DONE_STATUSES]
    to_do.sort(key=lambda task: task.status != Task.Status.IN_PROGRESS)  # stable: time order otherwise
    next_job = to_do[0] if to_do else None
    if next_job is not None:
        next_job.next_action = _next_technician_action(next_job) if next_job.my_role == 'lead' else None
        site = next_job.site
        next_job.directions_url = 'https://www.google.com/maps/search/?api=1&query=' + quote(
            f'{site.latitude},{site.longitude}' if site.latitude is not None else f'{site.name}, {site.address}',
        )
    return {
        'today': today,
        'next_job': next_job,
        'later_today': to_do[1:],
        'done_today': [task for task in todays if task.status not in NOT_DONE_STATUSES],
        'today_count': len(todays),
        'overdue': _overdue(mine, with_role, today),
    }


@login_required
def my_week(request):
    """My schedule: Today (the default — the next job first), or the same
    calendar as the Schedule page — week, month or year (?view=) — of
    just this person's own tasks, each marked with their role. Overdue
    jobs show above whichever is open.
    """
    technician = require_technician(request)
    if request.GET.get('view') in ('week', 'month', 'year') or 'start' in request.GET:
        mine, with_role = _my_tasks(technician)
        context = _calendar_context(request, mine, technician.country)
        for entry in context['days']:
            with_role(entry['tasks'])
        context['unscheduled'] = with_role(context['unscheduled'])
        context['overdue'] = _overdue(mine, with_role, timezone.localtime().date())
    else:
        context = {'view': 'today', **today_plan(technician)}
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
    return render(request, 'tasks/my_progress.html', progress_context(request, technician))


@login_required
def technician_progress(request, pk):
    """The same page as My progress, for one technician — opened by whoever
    can view technicians, in their country (Technicians, Team progress)."""
    requesting_technician = require_permission(request, RolePermission.Permission.VIEW_TECHNICIANS)
    technician = _scoped_or_404(
        _people_i_oversee(requesting_technician), pk, requesting_technician, get_active_country(request), 'country',
    )
    return render(request, 'tasks/my_progress.html', {
        **progress_context(request, technician), 'viewing_other': True, 'technician': technician,
    })


def progress_context(request, technician):
    """Everything My progress shows, for `technician`."""
    skills = _skills_with_current_rating(technician)
    conduct_areas = _conduct_areas_with_current_rating(technician)
    certification = _certification_status(technician)
    ninety_day = _ninety_day_progress(technician, certification)
    reliability = _reliability_stats(technician)

    leaderboard = _leaderboard(technician.country)
    rank = next((position for position, (t, _points) in enumerate(leaderboard, start=1) if t.pk == technician.pk), None)

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


def _completed_in_month(technician, month_first):
    """How many tasks someone was on (lead or helper) had their first work
    report in that month — the figure My progress's line counts."""
    start, end = _month_window(month_first)
    mine = TaskAssignment.objects.filter(technician=technician).values('task_id')
    return TaskEvent.objects.filter(
        event_type=TaskEvent.EventType.REPORT_SUBMITTED, task_id__in=mine,
    ).values('task_id').annotate(first=Min('occurred_at')).filter(first__gte=start, first__lt=end).count()


@login_required
def team_progress(request):
    """The field team the viewer oversees, side by side, for a month
    (?month=YYYY-MM): rank, tasks completed (against the month before),
    hours, certification, and a small line of their year. Each opens
    their Progress page.
    """
    viewer = require_permission(request, RolePermission.Permission.VIEW_TECHNICIANS)
    this_month = timezone.localtime().date().replace(day=1)
    month_first = min(_resolve_report_month(request), this_month)
    return render(request, 'tasks/team_progress.html', {
        'rows': team_progress_rows(viewer, get_active_country(request), month_first),
        'month_first': month_first,
        'prev_month': _shift_month(month_first, -1),
        'next_month': _shift_month(month_first, 1) if month_first < this_month else None,
        'is_this_month': month_first == this_month,
    })


def team_progress_rows(viewer, country, month_first):
    """Team progress's rows — the field team `viewer` oversees in
    `country`, by rank. Shared with the app (api.views.TeamProgressView)."""
    previous = _shift_month(month_first, -1)
    ranks = {t.pk: position for position, (t, _points) in enumerate(_leaderboard(country), start=1)}
    hours = {row['technician'].pk: row['hours'] for row in _technician_hours_rows(country, month_first)}
    rows = []
    # The field team: a manager or admin sees supervisors here too.
    for technician in _people_i_oversee(viewer).filter(
        country=country, is_active=True, role__in=[Technician.Role.SUPERVISOR, Technician.Role.TECHNICIAN],
    ).select_related('user').order_by('full_name'):
        completed = _completed_in_month(technician, month_first)
        certification = _certification_status(technician)
        rows.append({
            'technician': technician,
            'rank': ranks.get(technician.pk),
            'completed': completed,
            'change': completed - _completed_in_month(technician, previous),
            'hours': hours.get(technician.pk, 0),
            'certified': certification['non_cardio_certified'] + certification['conduct_certified'],
            'required': certification['non_cardio_total'] + certification['conduct_total'],
            'is_certified': certification['is_certified'],
            'trend': _my_trend_chart(technician),
        })
    rows.sort(key=lambda row: (row['rank'] is None, row['rank'] or 0, row['technician'].full_name))
    for row in rows:
        row['change_size'] = abs(row['change'])
    return rows


def save_skill_self_rating(technician, skill, cleaned, today):
    """A technician's own rating of a skill, with its evidence — the
    current rating plus its history row. My skills on the website and in
    the app (api.views.MySkillsView). Check first that it's still theirs
    to change (not yet reviewed by a supervisor).
    """
    level, evidence, note = int(cleaned['level']), cleaned['evidence'], cleaned.get('note', '')
    with transaction.atomic():
        TechnicianSkill.objects.update_or_create(
            technician=technician, skill=skill, defaults=dict(
                level=level, evidence=evidence, source=TechnicianSkill.Source.SELF,
                set_by=technician, set_on=today, note=note,
            ),
        )
        # The same uploaded file gets saved twice (current snapshot +
        # history log) — its read pointer is at the end after the first
        # save, so it must be rewound or the history row gets an empty file.
        evidence.seek(0)
        TechnicianSkillAssessment.objects.create(
            technician=technician, skill=skill, level=level, evidence=evidence,
            source=TechnicianSkill.Source.SELF, set_by=technician, set_on=today, note=note,
        )


def save_conduct_self_rating(technician, area, cleaned, today):
    """The same for a conduct area (no evidence)."""
    level = int(cleaned['level'])
    with transaction.atomic():
        TechnicianConduct.objects.update_or_create(
            technician=technician, conduct_area=area, defaults=dict(
                level=level, source=TechnicianConduct.Source.SELF, set_by=technician, set_on=today,
            ),
        )
        TechnicianConductAssessment.objects.create(
            technician=technician, conduct_area=area, level=level,
            source=TechnicianConduct.Source.SELF, set_by=technician, set_on=today,
        )


def skills_summary(technician):
    """Someone's skills as plain data, for the app: progress toward
    certification, what each level means, and each basic / advanced skill
    and conduct area with its level and status. Same rules and figures as
    the My skills page.
    """
    skills = _skills_with_current_rating(technician)
    areas = _conduct_areas_with_current_rating(technician)
    basic, advanced = _split_by_category(skills)
    confirmed = TechnicianSkill.Source.SUPERVISOR
    certification = _certification_status(technician)

    def row(item, kind):
        current = item.current
        return {
            'kind': kind, 'id': item.pk, 'name': item.display_name,
            'level': current.level if current else None,
            'level_label': str(SKILL_LEVEL_SHORT[current.level]) if current else '',
            # 'none' (not rated), 'self' (waiting for review) or 'confirmed'.
            'status': 'none' if current is None else ('confirmed' if current.source == confirmed else 'self'),
            'confirmed_by': current.set_by.full_name if current and current.source == confirmed and current.set_by else '',
            'note': current.note if current else '',
            'evidence_url': current.evidence.url if kind == 'skill' and current and current.evidence else '',
            'can_rate': current is None or current.source != confirmed,
        }

    return {
        'progress': {
            'basic_confirmed': certification['non_cardio_certified'], 'basic_total': certification['non_cardio_total'],
            'advanced_confirmed': sum(
                1 for skill in advanced
                if skill.current and skill.current.source == confirmed and skill.current.level >= RELIABLE_LEVEL
            ),
            'advanced_total': len(advanced),
            'conduct_confirmed': certification['conduct_certified'], 'conduct_total': certification['conduct_total'],
            'waiting': sum(1 for item in skills + areas if item.current and item.current.source != confirmed),
            'is_certified': certification['is_certified'],
            'reliable_level': RELIABLE_LEVEL,
        },
        'levels': [
            {'level': level, 'short': str(SKILL_LEVEL_SHORT[level]), 'text': str(text)}
            for level, text in sorted(SKILL_LEVEL_RUBRIC.items())
        ],
        'basic': [row(skill, 'skill') for skill in basic],
        'advanced': [row(skill, 'skill') for skill in advanced],
        'conduct': [row(area, 'conduct') for area in areas],
    }


@login_required
def my_skills(request):
    """Self-assessment — a technician's own first guess at each skill and
    conduct area, using the same rubric a supervisor later reviews it
    against. A self-rating never counts toward certification on its own,
    and once set it can only be changed by a supervisor from here on.
    """
    technician = require_technician(request)
    # A submission that didn't save, shown again open with its errors:
    # ('skill' or 'conduct', its id, the bound form).
    failed = None

    if request.method == 'POST':
        action = request.POST.get('action')
        today = timezone.localtime().date()

        if action == 'rate_skill':
            skill = get_object_or_404(Skill, pk=request.POST.get('skill_id'), is_active=True)
            form = SelfRateSkillForm(request.POST, request.FILES)
            current = TechnicianSkill.objects.filter(technician=technician, skill=skill).first()
            # Their own rating can be replaced until a supervisor reviews it.
            if current is not None and current.source != TechnicianSkill.Source.SELF:
                messages.error(request, _('A supervisor has reviewed this skill, so only a supervisor can change it now.'))
                return redirect('tasks:my_skills')
            if form.is_valid():
                save_skill_self_rating(technician, skill, form.cleaned_data, today)
                messages.success(request, _('Self-rating saved.'))
                return redirect(f"{reverse('tasks:my_skills')}#skill-{skill.pk}")
            failed = ('skill', skill.pk, form)

        elif action == 'rate_conduct':
            area = get_object_or_404(ConductArea, pk=request.POST.get('conduct_area_id'), is_active=True)
            form = SelfRateLevelForm(request.POST)
            current = TechnicianConduct.objects.filter(technician=technician, conduct_area=area).first()
            if current is not None and current.source != TechnicianConduct.Source.SELF:
                messages.error(request, _('A supervisor has reviewed this area, so only a supervisor can change it now.'))
                return redirect('tasks:my_skills')
            if form.is_valid():
                save_conduct_self_rating(technician, area, form.cleaned_data, today)
                messages.success(request, _('Self-rating saved.'))
                return redirect(f"{reverse('tasks:my_skills')}#conduct-{area.pk}")
            failed = ('conduct', area.pk, form)
        if failed:
            messages.error(request, _('Your self-rating was not saved. Please correct the form below.'))

    skills = _skills_with_current_rating(technician)
    conduct_areas = _conduct_areas_with_current_rating(technician)
    for kind, items in (('skill', skills), ('conduct', conduct_areas)):
        for item in items:
            item.level_label = SKILL_LEVEL_SHORT[item.current.level] if item.current else ''
            item.failed_form = failed[2] if failed and failed[:2] == (kind, item.pk) else None
    basic_skills, cardio_skills = _split_by_category(skills)
    certification = _certification_status(technician)
    confirmed = TechnicianSkill.Source.SUPERVISOR
    context = {
        'basic_skills': basic_skills,
        'cardio_skills': cardio_skills,
        'conduct_areas': conduct_areas,
        'self_rate_skill_form': SelfRateSkillForm(),
        'self_rate_form': SelfRateLevelForm(),
        'rubric': sorted(SKILL_LEVEL_RUBRIC.items()),
        'reliable_level': RELIABLE_LEVEL,
        'certification': certification,
        'cardio_confirmed': sum(
            1 for skill in cardio_skills
            if skill.current and skill.current.source == confirmed and skill.current.level >= RELIABLE_LEVEL
        ),
        'waiting_count': sum(
            1 for item in skills + conduct_areas if item.current and item.current.source != confirmed
        ),
    }
    return render(request, 'tasks/my_skills.html', context)


def sign_out_of_mobile_app(user):
    """When a manager steps in (a temporary password, "Sign out of every
    phone", a login turned off): the app's sign-in token stops working and
    that phone stops getting push notifications, so a lost or handed-on
    phone can't stay signed in. Not when someone changes their own
    password — that would sign them out of their own phone for nothing.
    """
    Token.objects.filter(user=user).delete()
    PushDevice.objects.filter(technician__user=user).delete()


def send_password_changed_email(user, temporary=False):
    """Tells someone their password changed, so they notice if it wasn't
    them. In English, like the staff login email. Nothing to send without
    an email address.
    """
    if not user.email:
        return
    technician = getattr(user, 'technician', None)
    contact = technician.full_name if technician else user.get_username()
    context = {'contact': contact, 'username': user.get_username(), 'temporary': temporary}
    with translation.override('en'):
        subject = _('Your Profit Sports Solutions password has been changed')
        if temporary:
            what = _(
                'A manager has set a temporary password for your Profit Sports Solutions account (username: '
                '%(username)s). You will be asked to choose your own password at your next sign-in, and you '
                'have been signed out of the mobile app.'
            )
        else:
            what = _(
                'The password for your Profit Sports Solutions account (username: %(username)s) has been '
                'changed.'
            )
        message = _(
            'Dear %(contact)s,\n\n'
            '%(what)s\n\n'
            'If you did not make this change, please contact your manager immediately.\n\n'
            'Best regards,\n'
            'Profit Sports Solutions\n'
        ) % {'contact': contact, 'what': what % {'username': user.get_username()}}
    _send_notification_email(subject, message, 'tasks/email/password_changed.html', context, [user.email])


@login_required
def my_profile(request):
    """Everyone's own account screen: photo, language, email and password.
    Field staff also get a snapshot of their standing (certification,
    90-day track, ranking) that My progress covers in full, and who to go
    to above them. Managers and admins have neither, so they don't see
    those sections.
    """
    technician = require_technician(request)

    if request.method == 'POST' and request.POST.get('action') == 'change_password':
        profile_form = MyProfileForm(instance=technician)
        password_form = ChangePasswordForm(request.user, request.POST)
        if password_form.is_valid():
            password_form.save()
            update_session_auth_hash(request, request.user)
            # Their own change: phones stay signed in. The email is how a
            # change they didn't make gets noticed (then a manager signs
            # every phone out from Staff accounts).
            send_password_changed_email(request.user)
            messages.success(request, _('Password changed.'))
            return redirect('tasks:my_profile')
    elif request.method == 'POST':
        password_form = ChangePasswordForm(request.user)
        profile_form = MyProfileForm(request.POST, request.FILES, instance=technician)
        if profile_form.is_valid():
            profile_form.save()
            messages.success(request, _('Profile updated.'))
            return redirect('tasks:my_profile')
    else:
        profile_form = MyProfileForm(instance=technician)
        password_form = ChangePasswordForm(request.user)

    context = {
        'technician': technician,
        'profile_form': profile_form,
        'password_form': password_form,
        'is_field_staff': not technician.is_manager_tier,
    }
    if not technician.is_manager_tier:
        certification = _certification_status(technician)
        leaderboard = _leaderboard(technician.country)
        context.update({
            'certification': certification,
            'ninety_day': _ninety_day_progress(technician, certification),
            'rank': next(
                (position for position, (t, _points) in enumerate(leaderboard, start=1) if t.pk == technician.pk),
                None,
            ),
            'leaderboard_size': len(leaderboard),
        })
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
            messages.success(request, _('User added.'))
            return redirect('tasks:technician_list')
    else:
        form = TechnicianCreateForm()

    return render(request, 'tasks/technician_create.html', {'form': form})


def overseen_roles(viewer):
    """Whose records `viewer` can open — the list, each person's progress,
    schedule, skills and edit page: an admin everyone (None); any manager
    the supervisors and technicians; anyone else the technicians.
    """
    if viewer.role == Technician.Role.ADMIN:
        return None
    if viewer.is_any_manager:
        return [Technician.Role.SUPERVISOR, Technician.Role.TECHNICIAN]
    return [Technician.Role.TECHNICIAN]


def _people_i_oversee(viewer):
    """Technician.objects narrowed to overseen_roles(viewer)."""
    roles = overseen_roles(viewer)
    return Technician.objects.all() if roles is None else Technician.objects.filter(role__in=roles)


def _mark_openable(people, viewer):
    """Set `.can_open` on each person: whether `viewer` oversees them, so a
    list showing everyone (the assign page, the new-task team panel) links
    only the schedules they can open. Returns `people`."""
    roles = overseen_roles(viewer)
    for person in people:
        person.can_open = roles is None or person.role in roles
    return people


def _overseen_rows(viewer, rows):
    """Hours rows ({'technician': ...}) kept to the people `viewer` oversees."""
    roles = overseen_roles(viewer)
    return rows if roles is None else [row for row in rows if row['technician'].role in roles]


@login_required
def technician_list(request):
    """The technician roster for a supervisor's own country — the "who can
    I rely on" view. Never existed as a screen before this feature.

    Shows active technicians by default; ?status=inactive switches to the
    deactivated ones instead (never both — an inactive technician doesn't
    belong on the "who can I rely on" list), so a manager can find someone
    to reactivate without them cluttering the roster everyone else uses.
    """
    viewer = require_permission(request, RolePermission.Permission.VIEW_TECHNICIANS)

    if request.method == 'POST' and request.POST.get('action') == 'reactivate':
        require_permission(request, RolePermission.Permission.MANAGE_TECHNICIANS)
        technician = get_object_or_404(
            _people_i_oversee(viewer), pk=request.POST.get('technician_id'), country=get_active_country(request),
        )
        technician.set_active(True)
        messages.success(request, _('Technician reactivated.'))
        return redirect(f"{reverse('tasks:technician_list')}?status=inactive")

    show_inactive = request.GET.get('status') == 'inactive'
    search = request.GET.get('q', '').strip()
    technicians = _people_i_oversee(viewer).filter(
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

    # Named, like its sidebar link, for who's in it (overseen_roles).
    if viewer.role == Technician.Role.ADMIN:
        page_title = _('Users')
    elif viewer.is_any_manager:
        page_title = _('My team')
    else:
        page_title = _('Technicians')
    context = {'rows': rows, 'search': search, 'show_inactive': show_inactive, 'page_title': page_title}
    return render(request, 'tasks/technician_list.html', context)


@login_required
def all_technicians(request):
    """Every active technician in every country, with the same
    certification/90-day columns as the regular roster — a manager's
    combined "everyone, everywhere" roster and certification-progress
    overview, so auditing someone doesn't mean switching active country
    first. Manager-only, same fixed floor as the other "all ..." boards.
    """
    viewer = require_manager(request)

    search = request.GET.get('q', '').strip()
    technicians = _people_i_oversee(viewer).filter(is_active=True).select_related('country').order_by('full_name')
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


def _technician_hours_rows(active_country, period_start_date):
    """One row per active technician in the country for that month: the
    work reports they filed as lead, the ones they helped on, and their
    hours — a report's hours count for everyone on the team, except where
    the lead entered a helper's own (helper_hours), plus any hours put in
    before a handover. Shared by the Hours page and its CSV export.
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
        'task__events',
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
            'overrun_count': sum(1 for report in own_reports if report.is_overrun),
            'above_tapped_count': sum(1 for report in own_reports if report.is_above_tapped),
            'away_count': sum(1 for report in own_reports if report.has_taps_away_from_site),
        })
    return rows


# A scheduled visit with no "Arrived" this long after its time is flagged
# on the team map.
NO_ARRIVAL_GRACE = timedelta(minutes=30)


@login_required
def team_map(request):
    """Where each technician last tapped today, on a map, and what needs a
    follow-up: a late arrival, a tap far from the site, a job running past
    its estimate. Built only from the location taken at each tap
    (tasks.location) — nobody is tracked between taps.
    """
    require_permission(request, RolePermission.Permission.VIEW_TECHNICIANS)
    return render(request, 'tasks/team_map.html', team_map_data(get_active_country(request)))


def team_map_data(country):
    """The team map's content for a country: each technician with their
    last tap today, the pins, and what needs a follow-up (late arrivals,
    taps away from the site, jobs past their estimate). The website's Team
    map and the app's (api.views.TeamMapView).
    """
    now = timezone.now()
    today = timezone.localdate()

    technicians = list(Technician.objects.filter(
        is_active=True, country=country, role=Technician.Role.TECHNICIAN, user__isnull=False,
    ).order_by('full_name'))
    names = {technician.user_id: technician.full_name for technician in technicians}
    last_taps = {}
    for tap in (
        TaskEvent.objects.filter(actor_id__in=names, occurred_at__date=today, latitude__isnull=False)
        .select_related('task__site__customer').order_by('-occurred_at')
    ):
        last_taps.setdefault(tap.actor_id, tap)

    pins = []
    for technician in technicians:
        tap = last_taps.get(technician.user_id)
        technician.last_tap = tap
        if tap is None:
            continue
        place = f'{tap.task.site.customer.name}, {tap.task.site.name}'
        detail = _('%(event)s at %(time)s') % {
            'event': tap.get_event_type_display(), 'time': formats.time_format(timezone.localtime(tap.occurred_at)),
        }
        pins.append({
            'name': technician.full_name, 'detail': detail, 'place': place,
            'lat': float(tap.latitude), 'lng': float(tap.longitude),
            'away': tap.location_status == TaskEvent.LocationStatus.AWAY,
            'url': reverse('tasks:task_detail', args=[tap.task_id]), 'task_id': tap.task_id,
        })

    open_tasks = Task.objects.filter(status__in=OPEN_STATUSES, site__customer__country=country)
    attention = []
    late = _with_lead_prefetch(
        open_tasks.filter(
            status__in=[Task.Status.ASSIGNED, Task.Status.ACCEPTED],
            scheduled_for__date=today, scheduled_for__lte=now - NO_ARRIVAL_GRACE,
        ).exclude(Exists(TaskEvent.objects.filter(
            task=OuterRef('pk'), event_type=TaskEvent.EventType.ARRIVED, occurred_at__date=today,
        )))
        .select_related('site__customer').order_by('scheduled_for'),
    )
    _attach_lead_technician(late)
    for task in late:
        attention.append({
            'text': _('%(name)s has not yet arrived at %(site)s (scheduled for %(time)s).') % {
                'name': task.lead_technician.full_name if task.lead_technician else _('No one assigned'),
                'site': task.site.name, 'time': formats.time_format(timezone.localtime(task.scheduled_for)),
            },
            'url': reverse('tasks:task_detail', args=[task.pk]), 'task_id': task.pk, 'level': 'critical',
        })
    for tap in (
        TaskEvent.objects.filter(
            occurred_at__date=today, location_status=TaskEvent.LocationStatus.AWAY,
            task__site__customer__country=country,
        ).select_related('task__site', 'actor__technician').order_by('-occurred_at')
    ):
        attention.append({
            'text': _('%(name)s recorded “%(event)s” %(distance)s away from %(site)s.') % {
                'name': tap.actor.technician.full_name if hasattr(tap.actor, 'technician') else tap.actor.get_username(),
                'event': tap.get_event_type_display(), 'distance': tap.distance_display, 'site': tap.task.site.name,
            },
            'url': reverse('tasks:task_detail', args=[tap.task_id]), 'task_id': tap.task_id, 'level': 'warning',
        })
    running = open_tasks.filter(status=Task.Status.IN_PROGRESS, estimated_hours__isnull=False).annotate(
        working_since=Max('events__occurred_at', filter=Q(
            events__event_type__in=[TaskEvent.EventType.STARTED, TaskEvent.EventType.RESUMED],
        )),
    ).select_related('site')
    for task in running:
        if task.working_since and now - task.working_since > timedelta(hours=float(task.estimated_hours)):
            attention.append({
                'text': _('The job at %(site)s has exceeded its estimate of %(hours)s hours.') % {
                    'site': task.site.name, 'hours': formats.number_format(task.estimated_hours, decimal_pos=1),
                },
                'url': reverse('tasks:task_detail', args=[task.pk]), 'task_id': task.pk, 'level': 'warning',
            })

    return {'technicians': technicians, 'pins': pins, 'attention': attention, 'today': today}


@login_required
def technician_hours(request):
    """Labour hours from filed work reports, per technician, for one month
    in the active country — how a supervisor/manager/admin follows each
    technician's actual workload. A report's hours count for each person
    on the team (see _technician_hours_rows), so the total is person-hours.
    Every active technician gets a row, so someone with nothing filed
    shows up as zero rather than going unnoticed.
    """
    viewer = require_permission(request, RolePermission.Permission.VIEW_TECHNICIANS)
    period_start_date = _resolve_report_month(request)
    rows = _overseen_rows(viewer, _technician_hours_rows(get_active_country(request), period_start_date))

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
    viewer = require_permission(request, RolePermission.Permission.VIEW_TECHNICIANS)
    active_country = get_active_country(request)
    period_start_date = _resolve_report_month(request)
    rows = _overseen_rows(viewer, _technician_hours_rows(active_country, period_start_date))

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = (
        f'attachment; filename="technician-hours-{active_country.iso_code}-{period_start_date:%Y-%m}.csv"'
    )
    writer = csv.writer(response)

    writer.writerow([f'Technician hours — {active_country.name} — {period_start_date:%B %Y}'])
    writer.writerow([
        'Technician', 'Reports filed', 'Labour hours', 'Estimated hours', 'Well over estimate', 'Helped on',
        'Above tapped time', 'Taps away from the site',
    ])
    for row in rows:
        writer.writerow([
            row['technician'].full_name, len(row['reports']), row['hours'], row['estimated'] or '',
            row['overrun_count'], row['helped_on'], row['above_tapped_count'], row['away_count'],
        ])

    writer.writerow([])
    writer.writerow(['Reports'])
    writer.writerow([
        'Technician', 'Task number', 'Customer', 'Submitted at', 'Labour hours', 'Estimated hours',
        'Well over estimate', 'Tapped hours', 'Above tapped time', 'Taps away from the site',
    ])
    for row in rows:
        for report in row['reports']:
            tapped = report.tapped_hours
            writer.writerow([
                row['technician'].full_name, report.task.task_number, report.task.site.customer.name,
                timezone.localtime(report.submitted_at).strftime('%Y-%m-%d %H:%M'), report.labour_hours,
                report.task.estimated_hours or '', 'yes' if report.is_overrun else '',
                '' if tapped is None else tapped, 'yes' if report.is_above_tapped else '',
                'yes' if report.has_taps_away_from_site else '',
            ])

    if any(row['helped_reports'] for row in rows):
        writer.writerow([])
        writer.writerow(['Hours as a helper'])
        writer.writerow(['Technician', 'Task number', 'Customer', 'Submitted at', 'Labour hours'])
        for row in rows:
            for report, hours in row['helped_reports']:
                writer.writerow([
                    row['technician'].full_name, report.task.task_number, report.task.site.customer.name,
                    timezone.localtime(report.submitted_at).strftime('%Y-%m-%d %H:%M'), hours,
                ])

    if any(row['handovers'] for row in rows):
        writer.writerow([])
        writer.writerow(['Hours before a handover'])
        writer.writerow(['Technician', 'Task number', 'Customer', 'Taken off at', 'Labour hours'])
        for row in rows:
            for assignment in row['handovers']:
                writer.writerow([
                    row['technician'].full_name, assignment.task.task_number, assignment.task.site.customer.name,
                    timezone.localtime(assignment.ended_at).strftime('%Y-%m-%d %H:%M'), assignment.labour_hours,
                ])

    return response


@login_required
def technician_board(request, pk):
    """A supervisor's or manager's view of one person's schedule — the
    same calendar as My schedule (week, month or year) of their own tasks,
    with their overdue jobs; scoped to the viewer's country as everywhere
    else a technician is looked at.
    """
    requesting_technician = require_permission(request, RolePermission.Permission.VIEW_TECHNICIANS)
    technician = _scoped_or_404(
        _people_i_oversee(requesting_technician), pk, requesting_technician, get_active_country(request), 'country',
    )
    mine, with_role = _my_tasks(technician)
    context = _calendar_context(request, mine, technician.country)
    for entry in context['days']:
        with_role(entry['tasks'])
    context['unscheduled'] = with_role(context['unscheduled'])
    context['overdue'] = _overdue(mine, with_role, timezone.localtime().date())
    context['technician'] = technician
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


def close_task_directly(task, user, note):
    """A manager closing a task outright (any status but closed), with a note."""
    task.status = Task.Status.CLOSED
    task.save(update_fields=['status'])
    TaskEvent.objects.create(
        task=task, event_type=TaskEvent.EventType.CLOSED, occurred_at=timezone.now(), actor=user, note=note,
    )


def cancel_task(task, user, note):
    """A manager cancelling a task before its report (CANCELLABLE_STATUSES),
    with the reason; everyone on it gets a push."""
    task.status = Task.Status.CANCELLED
    task.save(update_fields=['status'])
    TaskEvent.objects.create(
        task=task, event_type=TaskEvent.EventType.CANCELLED, occurred_at=timezone.now(), actor=user, note=note,
    )
    for assignment in task.assignments.filter(is_active=True).select_related('technician'):
        push_cancelled(assignment.technician, task)


def flag_negligence(task, user, note):
    """A manager flagging a closed task's lead for negligence, with what happened."""
    TaskEvent.objects.create(
        task=task, event_type=TaskEvent.EventType.NEGLIGENCE, occurred_at=timezone.now(), actor=user, note=note,
    )


def correct_tap_time(task, event, user, cleaned):
    """A manager correcting a recorded tap's time (CorrectEventTimeForm),
    with the reason. The visit's hours come from these times
    (Task.time_on_site), so a filed report's are worked out again."""
    event.corrected_at = cleaned['corrected_at']
    event.corrected_by = user
    event.correction_reason = cleaned['reason']
    event.save(update_fields=['corrected_at', 'corrected_by', 'correction_reason'])
    report = getattr(task, 'report', None)
    if report is not None and report.submitted_at:
        report.labour_hours = task.time_on_site() or report.labour_hours
        report.save(update_fields=['labour_hours'])


def save_skill_review(technician, skill, supervisor, cleaned, today):
    """A supervisor confirming (or adjusting) a skill level — it always
    overwrites what was there, and goes in the history. The review screen
    on the website and the person's page in the app."""
    level, note = int(cleaned['level']), cleaned.get('note', '')
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


def save_conduct_review(technician, area, supervisor, cleaned, today):
    """The same for a conduct area."""
    level, note = int(cleaned['level']), cleaned.get('note', '')
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


def set_availability(technician, available, reason=''):
    """Available for work, or not (with a reason) — for every task at
    once. The Availability page on the website and the person's page in
    the app."""
    technician.is_available = available
    technician.unavailable_reason = '' if available else reason
    technician.save(update_fields=['is_available', 'unavailable_reason'])


@login_required
def technician_skills(request, pk):
    """A supervisor's review screen for one technician — confirm or
    override every self-rating against real evidence. A level only ever
    changes here, by a deliberate supervisor action; nothing computed
    writes to it automatically.
    """
    supervisor = require_permission(request, RolePermission.Permission.REVIEW_SKILLS)
    technician = _scoped_or_404(_people_i_oversee(supervisor), pk, supervisor, get_active_country(request), 'country')

    if request.method == 'POST':
        action = request.POST.get('action')
        today = timezone.localtime().date()

        if action == 'review_skill':
            skill = get_object_or_404(Skill, pk=request.POST.get('skill_id'), is_active=True)
            form = ReviewLevelForm(request.POST)
            if form.is_valid():
                save_skill_review(technician, skill, supervisor, form.cleaned_data, today)
                messages.success(request, _('Level confirmed.'))
                return redirect('tasks:technician_skills', pk=technician.pk)

        elif action == 'review_conduct':
            area = get_object_or_404(ConductArea, pk=request.POST.get('conduct_area_id'), is_active=True)
            form = ReviewLevelForm(request.POST)
            if form.is_valid():
                save_conduct_review(technician, area, supervisor, form.cleaned_data, today)
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


def _tasks_to_reassign(technician):
    """Open tasks a (deactivated) person is still on, lead or helper."""
    return list(
        Task.objects.filter(
            assignments__technician=technician, assignments__is_active=True,
            status__in=NEEDS_REASSIGNING_STATUSES,
        ).select_related('site__customer').distinct().order_by('scheduled_for'),
    )


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
        _people_i_oversee(requesting_technician), pk, requesting_technician, get_active_country(request), 'country',
    )
    # Someone below this person can look, but not change anything — an
    # email or password change would let them sign in as them.
    if request.method == 'POST' and not can_manage_account(requesting_technician, technician):
        raise PermissionDenied
    was_last_admin = is_last_active_admin(technician) if technician.is_active else False

    form = TechnicianEditForm(instance=technician, is_manager=is_manager, is_admin=is_admin)
    deactivate_form = DeactivateTechnicianForm()
    trip_form = TechnicianTripForm(home_country=technician.country) if is_manager else None

    if request.method == 'POST' and request.POST.get('action') == 'add_trip' and is_manager:
        trip_form = TechnicianTripForm(request.POST, home_country=technician.country)
        if trip_form.is_valid():
            trip = trip_form.save(commit=False)
            trip.technician = technician
            trip.created_by = request.user
            trip.created_at = timezone.now()
            trip.save()
            messages.success(request, _('Trip added.'))
            return redirect('tasks:technician_edit', pk=technician.pk)

    if request.method == 'POST' and request.POST.get('action') == 'cancel_trip' and is_manager:
        trip = get_object_or_404(
            technician.trips, pk=request.POST.get('trip_id'), end_date__gte=timezone.localdate(),
        )
        trip.delete()
        messages.success(request, _('Trip cancelled.'))
        return redirect('tasks:technician_edit', pk=technician.pk)

    can_manage = is_manager and can_manage_account(requesting_technician, technician)
    login_form = CreateTechnicianLoginForm() if can_manage and technician.user_id is None else None
    password_form = NewPasswordForm(user=technician.user) if can_manage and technician.user_id else None

    if request.method == 'POST' and request.POST.get('action') == 'deactivate' and is_manager:
        deactivate_form = DeactivateTechnicianForm(request.POST)
        if was_last_admin:
            messages.error(request, _('This is the only administrator able to sign in, so they cannot be '
                                      'deactivated.'))
            return redirect('tasks:technician_edit', pk=technician.pk)
        if deactivate_form.is_valid():
            technician.set_active(False, reason=deactivate_form.cleaned_data['reason'])
            log_account_action(request, technician, f'Deactivated: {deactivate_form.cleaned_data["reason"]}')
            if _tasks_to_reassign(technician):
                messages.success(request, _('Deactivated. Please reassign their open tasks below.'))
                return redirect('tasks:technician_edit', pk=technician.pk)
            messages.success(request, _('Technician deactivated.'))
            return redirect('tasks:technician_list')

    elif request.method == 'POST' and request.POST.get('action') == 'create_login' and login_form is not None:
        login_form = CreateTechnicianLoginForm(request.POST)
        if login_form.is_valid():
            email = login_form.cleaned_data['email']
            # No password at all until they set one from the emailed link.
            user = get_user_model().objects.create_user(username=email, email=email)
            technician.user = user
            technician.must_change_password = True
            technician.save(update_fields=['user', 'must_change_password'])
            send_staff_setup_link(request, technician)
            log_account_action(request, technician, f'Staff login created; setup link emailed to {email}')
            messages.success(request, _('Login created. A setup link has been sent to %(email)s.') % {'email': email})
            return redirect('tasks:technician_edit', pk=technician.pk)

    elif request.method == 'POST' and request.POST.get('action') == 'reset_password' and password_form is not None:
        password_form = NewPasswordForm(user=technician.user, data=request.POST)
        if password_form.is_valid():
            password_form.save()
            # Temporary: they choose their own at their next sign-in, so
            # the manager who typed it doesn't keep knowing it.
            technician.must_change_password = True
            technician.save(update_fields=['must_change_password'])
            sign_out_of_mobile_app(technician.user)
            send_password_changed_email(technician.user, temporary=True)
            log_account_action(request, technician, 'Staff temporary password set; signed out of the mobile app')
            messages.success(request, _('The password has been reset. They will be asked to choose their own '
                                        'at their next sign-in.'))
            return redirect('tasks:technician_edit', pk=technician.pk)

    elif request.method == 'POST':
        form = TechnicianEditForm(
            request.POST, request.FILES, instance=technician, is_manager=is_manager, is_admin=is_admin,
        )
        if form.is_valid() and was_last_admin and form.cleaned_data.get('role', Technician.Role.ADMIN) != Technician.Role.ADMIN:
            form.add_error('role', _('This is the only administrator able to sign in. Please assign the '
                                     'administrator role to someone else first.'))
        if form.is_valid():
            form.save()
            messages.success(request, _('Technician updated.'))
            return redirect('tasks:technician_list')

    context = {
        'technician': technician, 'form': form, 'deactivate_form': deactivate_form,
        'login_form': login_form, 'password_form': password_form, 'is_manager': is_manager,
        'trip_form': trip_form, 'trips': technician.trips.select_related('country'),
        'tasks_to_reassign': _tasks_to_reassign(technician) if not technician.is_active else [],
        'today': timezone.localdate(),
    }
    return render(request, 'tasks/technician_edit.html', context)


@login_required
def first_login(request):
    """A one-time stop between signing in on a temporary password and
    reaching the home page — just setting a real password. Everything
    else about a technician's own record stays editable any time from
    My profile, not gated here.
    """
    technician = require_technician(request)
    # Home, not the dashboard: a technician can't open the dashboard.
    if not technician.must_change_password:
        return redirect('home')

    if request.method == 'POST':
        form = TechnicianFirstLoginForm(request.POST, user=request.user)
        if form.is_valid():
            technician.must_change_password = False
            technician.save(update_fields=['must_change_password'])
            request.user.set_password(form.cleaned_data['new_password1'])
            request.user.save(update_fields=['password'])
            update_session_auth_hash(request, request.user)
            messages.success(request, _('Your password has been set. Welcome.'))
            return redirect('home')
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
        messages.success(request, _('You are now viewing %(country)s.') % {'country': country.display_name})

    next_url = request.POST.get('next', '')
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = reverse('tasks:dashboard')
    return redirect(next_url)


@login_required
def notification_settings(request):
    """Admin-only, like Roles: every message the system sends, and the
    switches for the automatic ones (people.models.NotificationSettings).
    Messages someone sends with a button, and the security emails, are
    listed with no switch.
    """
    require_admin(request)
    form = NotificationSettingsForm(request.POST or None, instance=NotificationSettings.load())
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, _('Notification settings updated.'))
        return redirect('tasks:notification_settings')
    return render(request, 'tasks/notification_settings.html', {'form': form})


@login_required
def role_permissions(request):
    """Admin-only: which role can do what. (Notifications have their own
    page, notification_settings.) Deliberately not gated by the configurable system it
    manages (require_admin, not require_permission) — otherwise a bad
    edit here could lock every role out of ever fixing it again. Also
    the one screen a manager doesn't get, unlike every other manager-tier
    screen (see require_admin).
    """
    require_admin(request)

    roles = [
        Technician.Role.TECHNICIAN, Technician.Role.SUPERVISOR, Technician.Role.MANAGER,
        Technician.Role.COUNTRY_SUPPORT, Technician.Role.SUPPORT_MANAGER, Technician.Role.OPERATIONS_MANAGER,
        Technician.Role.WAREHOUSE_MANAGER, Technician.Role.ADMIN,
    ]
    permissions = list(RolePermission.Permission)

    if request.method == 'POST':
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

    context = {'roles': roles, 'matrix': matrix}
    return render(request, 'tasks/role_permissions.html', context)


@login_required
def audit_log(request):
    """Who did what, anywhere in Django admin — add/change/delete, with
    the actor and a timestamp, for free from Django's own LogEntry.
    Admin-only, same reasoning as Roles & permissions: this is oversight
    of everyone, including other admins, so it stays off the manager-tier
    floor. Covers /admin/ actions, plus every account action from Staff
    and Customer accounts and the edit pages (customers.accounts.
    log_account_action); task actions have their own trail (TaskEvent).
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
        submitted_at__gte=period_start, submitted_at__lt=period_end, kind=CustomerTicket.Kind.CUSTOMER,
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
def part_list(request):
    """The parts catalogue (docs: part) — add one, upload a CSV, edit a
    description, switch one off or back on. Gated by manage_parts, so the
    warehouse manager can keep it up to date, not just the manager tier.
    """
    require_permission(request, RolePermission.Permission.MANAGE_PARTS)
    add_form = PartForm()
    import_form = PartImportForm()

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            add_form = PartForm(request.POST)
            if add_form.is_valid():
                add_form.save()
                messages.success(request, _('Part added.'))
                return redirect('tasks:part_list')
        elif action == 'import':
            import_form = PartImportForm(request.POST, request.FILES)
            if import_form.is_valid():
                added, updated = _import_parts(import_form.cleaned_data['file'])
                messages.success(
                    request, _('%(added)s parts added, %(updated)s updated.') % {'added': added, 'updated': updated},
                )
                return redirect('tasks:part_list')
        elif action in ('toggle_active', 'edit_description'):
            part = get_object_or_404(Part, pk=request.POST.get('part_id'))
            if action == 'toggle_active':
                part.is_active = not part.is_active
                part.save(update_fields=['is_active'])
            else:
                part.description = request.POST.get('description', '').strip()[:200]
                part.save(update_fields=['description'])
            messages.success(request, _('Part updated.'))
            return redirect('tasks:part_list')

    search = request.GET.get('q', '').strip()
    parts = Part.objects.all()
    if search:
        parts = parts.filter(Q(code__icontains=search) | Q(description__icontains=search))
    paginator = Paginator(parts.order_by('-is_active', 'code'), 50)
    context = {
        'page_obj': paginator.get_page(request.GET.get('page')), 'search': search,
        'add_form': add_form, 'import_form': import_form,
    }
    return render(request, 'tasks/part_list.html', context)


def _import_parts(uploaded_file):
    """Rows of code, description. A new code is added; an existing one gets
    its description updated (and is switched back on). A first row whose
    code reads "code" is taken as a header and skipped.
    """
    text = uploaded_file.read().decode('utf-8-sig', errors='replace')
    added = updated = 0
    for index, row in enumerate(csv.reader(text.splitlines())):
        if not row or not row[0].strip():
            continue
        code = row[0].strip().upper()[:50]
        if index == 0 and code == 'CODE':
            continue
        description = row[1].strip()[:200] if len(row) > 1 else ''
        part, created = Part.objects.get_or_create(code=code, defaults={'description': description})
        if created:
            added += 1
        elif description != part.description or not part.is_active:
            part.description = description or part.description
            part.is_active = True
            part.save(update_fields=['description', 'is_active'])
            updated += 1
    return added, updated


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
                        '“%(name)s” still has technicians, customers or tickets linked to it. Please '
                        'deactivate it instead of deleting it.'
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
                record_tap(task, event_type, request.user, request.POST)
                if new_status:
                    task.status = new_status
                    task.save(update_fields=['status'])
            messages.success(request, _('Updated.'))
            if request.POST.get('next') == 'today':  # tapped on My schedule's Today card
                return redirect('tasks:my_week')
            return redirect('tasks:my_task_detail', pk=task.pk)

        elif action == 'block' and can_block:
            block_form = BlockTaskForm(request.POST)
            if block_form.is_valid():
                with transaction.atomic():
                    task.status = Task.Status.BLOCKED
                    task.save(update_fields=['status'])
                    record_tap(
                        task, TaskEvent.EventType.BLOCKED, request.user, request.POST,
                        note=block_form.cleaned_data['note'],
                    )
                messages.success(request, _('Task marked as blocked.'))
                return redirect('tasks:my_task_detail', pk=task.pk)

        elif action == 'pause' and can_pause:
            pause_form = PauseTaskForm(request.POST)
            if pause_form.is_valid():
                record_tap(
                    task, TaskEvent.EventType.PAUSED, request.user, request.POST,
                    note=pause_form.cleaned_data['note'],
                )
                messages.success(request, _('Marked as stopped for today.'))
                return redirect('tasks:my_task_detail', pk=task.pk)

        elif action == 'resume' and can_resume:
            record_tap(task, TaskEvent.EventType.RESUMED, request.user, request.POST)
            messages.success(request, _('Marked as resumed.'))
            return redirect('tasks:my_task_detail', pk=task.pk)

        elif action == 'undo' and is_lead:
            if undo_last_tap(task, request.user):
                messages.success(request, _('Action undone.'))
            else:
                messages.error(request, _('This can no longer be undone. Please ask a manager to correct the '
                                          'time.'))
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


def _active_helpers(task):
    """The helpers on the job now — each gets an hours box on the report."""
    return list(
        task.assignments.filter(is_active=True, role=TaskAssignment.Role.HELPER).select_related('technician'),
    )


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
    # Worked out from the lead's taps, never typed (Task.time_on_site);
    # `filed_at` is when a report saved offline in the app was filed.
    filed_at = getattr(report_form, 'filed_at', None)
    saved_report.labour_hours = task.time_on_site(until=filed_at) or saved_report.labour_hours or 0
    signature = report_form.cleaned_data.get('signature')
    if signature:
        path = default_storage.save(f'signatures/{task.pk}/{signature.name}', signature)
        saved_report.signature_url = request.build_absolute_uri(default_storage.url(path))
    saved_report.save()

    for assignment, hours in report_form.helper_hours().items():
        assignment.labour_hours = hours
        assignment.save(update_fields=['labour_hours'])

    saved_report.parts_used.all().delete()
    for cleaned in part_rows:
        if cleaned.get('part_code') and cleaned.get('quantity'):
            PartUsed.objects.create(
                report=saved_report, part_code=cleaned['part_code'],
                description=cleaned.get('description', ''), quantity=cleaned['quantity'],
            )

    now = timezone.now()
    # Where the lead filed it from counts like any other tap; a manager
    # correcting it from the office doesn't.
    if task.assignments.filter(technician=technician, role=TaskAssignment.Role.LEAD, is_active=True).exists():
        record_tap(
            task, TaskEvent.EventType.REPORT_SUBMITTED, request.user, getattr(request, 'data', request.POST),
            occurred_at=now,
        )
    else:
        TaskEvent.objects.create(
            task=task, event_type=TaskEvent.EventType.REPORT_SUBMITTED, occurred_at=now, actor=request.user,
        )
    saved_report.first_submission = task.status not in (
        Task.Status.PENDING_SUPERVISOR_REVIEW, Task.Status.COMPLETED, Task.Status.CLOSED,
    )
    if signature:
        # The customer signed this time: keep exactly what they signed, once
        # everything (machines included, saved by the caller) is committed.
        from reports.pdf import freeze_signed_content
        transaction.on_commit(lambda: freeze_signed_content(saved_report))
    copy_to = report_form.cleaned_data.get('send_copy_to')
    if saved_report.first_submission and copy_to:
        # Only once the report is safely saved, so a rollback never sends a
        # copy of something that isn't there.
        transaction.on_commit(lambda: send_visit_report_copy(saved_report, copy_to))
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
        if task.status == Task.Status.PENDING_SUPERVISOR_REVIEW:
            # Once saved for good — a report preview is rolled back and sends nothing.
            transaction.on_commit(lambda: push_in_their_language(
                task.responsible_supervisor,
                lambda: _('Report awaiting your review'),
                lambda: _('%(number)s — submitted by %(name)s') % {
                    'number': task.task_number, 'name': technician.full_name,
                },
                {'type': 'team_task', 'task_id': task.pk},
            ))
    return saved_report


def report_problems(report, language):
    """What to fix before sending a report, for its review: machines
    without a finding or action, no work performed, and typed text that
    isn't in the report's language (`language`: 'ar' or 'en'). Warnings —
    the person can still send it.
    """
    from reports.templatetags.report_text import text_dir

    problems = []
    machines = list(report.task.task_assets.select_related('asset__brand'))
    if not machines:
        problems.append(_('No machines are recorded. Add each machine you worked on.'))
    typed = [
        (_('Overall findings'), report.findings), (_('Other work'), report.action_taken),
        (_('Other recommendation'), report.recommendation),
    ]
    for item in machines:
        name = f"{item.asset.brand.name if item.asset.brand_id else ''} {item.asset.model_name}".strip()
        if not item.finding_code and not item.note.strip():
            problems.append(_('%(machine)s: pick a finding.') % {'machine': name})
        if not item.action_code and not item.action.strip():
            problems.append(_('%(machine)s: pick the action taken.') % {'machine': name})
        typed += [(name, item.note), (name, item.action)]
    if not report.work_lines and not report.action_taken.strip():
        problems.append(_('Pick the work performed.'))
    expected = 'rtl' if language == 'ar' else 'ltr'
    language_name = _('Arabic') if language == 'ar' else _('English')
    for label, text in typed:
        if text and any(char.isalpha() for char in text) and text_dir(text) != expected:
            problems.append(_('%(where)s: “%(text)s” is not in %(language)s, but the report is in %(language)s.') % {
                'where': label, 'text': text.strip()[:60], 'language': language_name,
            })
    return problems


def report_preview(request, task, technician, report_form, part_rows, machine_rows=None):
    """(html, problems): the report exactly as it would be sent, saved
    for a moment and rolled back — nothing is kept, sent or notified. For
    the "Review report" step on the website and in the app.
    `machine_rows` is (existing rows, new rows), or None to leave them.
    """
    from reports.pdf import render_visit_report_html, report_language

    language = report_language(task.site.customer)
    with transaction.atomic():
        saved = save_work_report(request, task, technician, report_form, part_rows)
        if machine_rows is not None:
            save_task_machines(task, *machine_rows)
        problems = report_problems(saved, language)
        html = render_visit_report_html(saved, language=language, problems=problems)
        transaction.set_rollback(True)
    return html, problems


def report_saved_message(first_submission, task):
    if first_submission and task.status == Task.Status.PENDING_SUPERVISOR_REVIEW:
        return _("Report submitted — awaiting your supervisor's review.")
    if first_submission:
        return _('Report submitted — awaiting manager approval before the task is closed.')
    if task.status in (Task.Status.PENDING_SUPERVISOR_REVIEW, Task.Status.COMPLETED):
        return _('Report updated — approval still pending.')
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
        messages.error(request, _('This task is closed. Only a manager or administrator can now correct its report.'))
        return redirect('tasks:my_task_detail', pk=task.pk)

    if task.status not in REPORT_EDITABLE_STATUSES:
        messages.error(request, _('Please start work on this task before submitting a report.'))
        return redirect('tasks:my_task_detail', pk=task.pk)

    if task.status == Task.Status.IN_PROGRESS and _task_is_paused(task):
        messages.error(request, _('Please mark yourself as resumed before submitting the report.'))
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


def report_machine_initial(task):
    """The report's machine rows, as (existing, new) initial data: what the
    report already records, or before the first filing the machines the
    customer reported for this task: theirs ready for an outcome, any not
    in their machine list ready to register. Shared by the web report form
    and the app (api.views.MyTaskReportView).
    """
    task_assets = list(task.task_assets.all())
    if task_assets:
        return [
            {
                'asset': ta.asset_id, 'outcome': ta.outcome, 'note': ta.note,
                'finding_code': ta.finding_code, 'action_code': ta.action_code, 'action': ta.action,
            }
            for ta in task_assets
        ], []
    ticket_machines = list(task.ticket_machines.all())
    return (
        [{'asset': machine.asset_id} for machine in ticket_machines if machine.asset_id],
        [
            {'model_name': machine.model_name, 'serial_no': machine.serial_no, 'ticket_machine': machine.pk}
            for machine in ticket_machines if not machine.asset_id
        ],
    )


def save_task_machines(task, existing_rows, new_rows):
    """Replace the machines the report records with these cleaned rows
    (ExistingAssetOutcomeForm / NewAssetForm). A new machine is registered
    at the site; when it was a customer's unlisted ticket machine, that
    ticket machine now points at it, so their ticket shows its result.
    Blank rows are skipped.
    """
    TaskAsset.objects.filter(task=task).delete()

    def wording(cleaned):
        return {
            'note': cleaned.get('note', ''), 'finding_code': cleaned.get('finding_code', ''),
            'action_code': cleaned.get('action_code', ''), 'action': cleaned.get('action', ''),
        }

    for cleaned in existing_rows:
        if cleaned.get('asset') and cleaned.get('outcome'):
            TaskAsset.objects.create(task=task, asset=cleaned['asset'], outcome=cleaned['outcome'], **wording(cleaned))
    for cleaned in new_rows:
        if cleaned.get('brand') and cleaned.get('model_name') and cleaned.get('outcome'):
            new_asset = Asset.objects.create(
                site=task.site, brand=cleaned['brand'], model_name=cleaned['model_name'],
                serial_no=cleaned.get('serial_no', ''),
            )
            TaskAsset.objects.create(task=task, asset=new_asset, outcome=cleaned['outcome'], **wording(cleaned))
            if cleaned.get('ticket_machine'):
                task.ticket_machines.filter(pk=cleaned['ticket_machine'], asset__isnull=True).update(asset=new_asset)


def _report_form(request, task, technician, back_url):
    """The report form itself (report, machines, parts), shared by the
    lead filing it and a manager correcting it. `back_url` is where both
    the back link and a successful save go.
    """
    report = getattr(task, 'report', None)
    parts = report.parts_used.all() if report else PartUsed.objects.none()

    existing_assets = list(Asset.objects.filter(site=task.site))
    ExistingAssetFormSet = formset_factory(ExistingAssetOutcomeForm, extra=EXISTING_ASSET_ROWS)
    NewAssetFormSet = formset_factory(NewAssetForm, extra=NEW_ASSET_ROWS)
    PartFormSet = formset_factory(PartUsedItemForm, extra=PART_ROWS)

    existing_initial, new_initial = report_machine_initial(task)
    part_initial = [
        {
            'part_code': part.part_code, 'description': part.description, 'quantity': part.quantity,
            'unit_cost': part.unit_cost, 'currency_code': part.currency_code,
        }
        for part in parts
    ]

    helpers = _active_helpers(task)
    time_on_site = task.time_on_site()
    if request.method == 'POST':
        # "Preview report": the report as it would be sent, in a new tab —
        # nothing saved, and no signature needed yet.
        preview = 'preview' in request.POST
        data = request.POST
        if preview:
            data = request.POST.copy()
            data['signature_drawn'] = ''  # nothing is stored for a preview
        report_form = WorkReportForm(
            data, None if preview else request.FILES, instance=report,
            require_signature=_requires_signature(task) and not preview, helpers=helpers,
            time_on_site=time_on_site,
        )
        existing_formset = ExistingAssetFormSet(
            request.POST, initial=existing_initial, prefix='existing', form_kwargs={'site': task.site},
        )
        new_formset = NewAssetFormSet(request.POST, initial=new_initial, prefix='new')
        part_formset = PartFormSet(request.POST, prefix='parts')

        if (
            report_form.is_valid() and existing_formset.is_valid()
            and new_formset.is_valid() and part_formset.is_valid()
        ):
            if preview:
                html, _problems = report_preview(
                    request, task, technician, report_form, part_formset.cleaned_data,
                    (existing_formset.cleaned_data, new_formset.cleaned_data),
                )
                return HttpResponse(html)
            with transaction.atomic():
                saved_report = save_work_report(request, task, technician, report_form, part_formset.cleaned_data)

                save_task_machines(task, existing_formset.cleaned_data, new_formset.cleaned_data)

            messages.success(request, report_saved_message(saved_report.first_submission, task))
            return redirect(back_url)
    else:
        report_form = WorkReportForm(
            instance=report, require_signature=_requires_signature(task), helpers=helpers,
            copy_to_default=task.site.effective_contact_email or '', time_on_site=time_on_site,
        )
        existing_formset = ExistingAssetFormSet(
            initial=existing_initial, prefix='existing', form_kwargs={'site': task.site},
        )
        new_formset = NewAssetFormSet(initial=new_initial, prefix='new')
        part_formset = PartFormSet(initial=part_initial, prefix='parts')

    from reports.pdf import hours_text, report_language

    context = {
        'task': task,
        'report': report,
        'back_url': back_url,
        # What the customer reads it in — anything typed goes in this language.
        'report_language': report_language(task.site.customer),
        'catalogue_parts': Part.objects.filter(is_active=True),
        'can_edit': True,
        'report_form': report_form,
        'existing_formset': existing_formset,
        'has_existing_assets': bool(existing_assets),
        'new_formset': new_formset,
        'part_formset': part_formset,
        # "1:32": the visit's time from the taps (Task.time_on_site).
        'time_on_site': hours_text(time_on_site),
        'time_is_final': report is not None and report.submitted_at is not None,
    }
    return render(request, 'tasks/my_report_form.html', context)


INTERNAL_TICKET_KINDS = [CustomerTicket.Kind.STOCK, CustomerTicket.Kind.VENDOR, CustomerTicket.Kind.OTHER]


def _has_permission(technician, permission):
    return RolePermission.objects.filter(role=technician.role, permission=permission, allowed=True).exists()


@login_required
def ticket_open(request):
    """Staff opening a ticket (open_tickets): for a customer who phoned,
    messaged or emailed — then it's a normal customer ticket, reviewed the
    usual way and visible in their portal — or an internal one (stock
    request, vendor follow-up, other) for whoever it's assigned to. Which
    kind comes from ?kind=, so each form is plain HTML, no script.
    """
    technician = require_technician(request)
    # open_tickets covers every kind (customer tickets too); anyone else
    # above a technician — a supervisor included — creates internal tasks.
    kinds = CustomerTicket.Kind.choices
    if not _has_permission(technician, RolePermission.Permission.OPEN_TICKETS):
        if technician.role == Technician.Role.TECHNICIAN:
            raise PermissionDenied
        kinds = [(value, label) for value, label in kinds if value != CustomerTicket.Kind.CUSTOMER]
    country = get_active_country(request)
    kind = request.GET.get('kind', kinds[0][0])
    if kind not in [value for value, label in kinds]:
        kind = kinds[0][0]
    is_customer = kind == CustomerTicket.Kind.CUSTOMER

    def make_form(*args):
        if is_customer:
            return StaffTicketForm(*args, country=country)
        return InternalTicketForm(*args, kind=kind, country=country)

    form = make_form(request.POST, request.FILES) if request.method == 'POST' else make_form()
    if request.method == 'POST' and form.is_valid():
        now = timezone.now()
        ticket = form.save(commit=False)
        ticket.kind = kind
        ticket.country = country
        ticket.opened_by = request.user
        ticket.submitted_at = now
        if is_customer:
            site = form.cleaned_data['site']
            ticket.customer = site.customer
            ticket.site = site
            ticket.company_name = site.customer.name
            ticket.customer_code = site.customer.code
            ticket.site_description = site.name
            ticket.site_address = site.address
            ticket.contact_name = ticket.contact_name or site.effective_contact_name
            ticket.contact_phone = ticket.contact_phone or site.effective_contact_phone
            ticket.contact_email = ticket.contact_email or site.effective_contact_email or ''
        else:
            ticket.assigned_at = now
        with transaction.atomic():
            save_new_ticket(ticket)
            for uploaded_file in form.cleaned_data['attachments']:
                CustomerTicketAttachment.objects.create(ticket=ticket, file=uploaded_file, uploaded_at=now)
            if is_customer:
                TicketNotification.objects.create(
                    ticket=ticket, kind=TicketNotification.Kind.NEW_TICKET, created_at=now,
                )
        if is_customer:
            send_ticket_confirmation_to_customer(request, ticket)
            send_new_ticket_email_to_staff(request, ticket)
        elif ticket.assigned_to != technician:
            _push_internal_ticket(ticket.assigned_to, ticket, _('New internal task assigned to you'))
        if is_customer:
            messages.success(request, _('Ticket %(number)s opened.') % {'number': ticket.ticket_number})
        else:
            messages.success(request, _('Internal task %(number)s created.') % {'number': ticket.ticket_number})
        if not is_customer:
            return redirect('tasks:internal_ticket', pk=ticket.pk)
        if _has_permission(technician, RolePermission.Permission.MANAGE_TICKETS):
            return redirect('tasks:ticket_review', pk=ticket.pk)
        return redirect('home')

    return render(request, 'tasks/staff_ticket_new.html', {
        'form': form, 'kind': kind, 'kinds': kinds,
    })


def _push_internal_ticket(technician, ticket, title):
    push_in_their_language(
        technician,
        lambda: title,
        lambda: f'{ticket.ticket_number} — {ticket.subject}',
        {'type': 'internal_ticket', 'ticket_id': ticket.pk},
    )


def _visible_internal_tickets(technician, country):
    """Internal tickets this person may see: all of them for any manager
    or ticket reviewer (manage_tickets), otherwise the ones assigned to
    them or that they opened.
    """
    tickets = CustomerTicket.objects.filter(kind__in=INTERNAL_TICKET_KINDS, country=country)
    if technician.is_any_manager or _has_permission(technician, RolePermission.Permission.MANAGE_TICKETS):
        return tickets
    return tickets.filter(Q(assigned_to=technician) | Q(opened_by=technician.user))


@login_required
def internal_tickets(request):
    """Stock requests, vendor follow-ups and other internal tickets —
    open ones first, newest first."""
    technician = require_technician(request)
    if technician.role == Technician.Role.TECHNICIAN:
        raise PermissionDenied
    status = request.GET.get('status', 'open')
    tickets = _visible_internal_tickets(technician, get_active_country(request)).select_related(
        'assigned_to', 'opened_by__technician',
    ).order_by('-submitted_at')
    if status == 'open':
        tickets = tickets.filter(status=CustomerTicket.Status.NEW)
    elif status == 'closed':
        tickets = tickets.filter(status__in=[CustomerTicket.Status.CLOSED, CustomerTicket.Status.CONVERTED])
    return render(request, 'tasks/internal_tickets.html', {
        'tickets': tickets[:200], 'status': status,
        # Anyone who can see the list can create one (ticket_open).
        'can_open': True,
    })


@login_required
def internal_ticket(request, pk):
    """One internal ticket: notes back and forth, handing it to someone
    else, and closing it with the result. The assignee, the opener and
    ticket reviewers can all act on it."""
    technician = require_technician(request)
    ticket = get_object_or_404(
        _visible_internal_tickets(technician, get_active_country(request)).select_related(
            'assigned_to', 'opened_by__technician',
        ),
        pk=pk,
    )
    country = get_active_country(request)
    is_open = ticket.status == CustomerTicket.Status.NEW
    note_form = InternalTicketNoteForm()
    reassign_form = ReassignInternalTicketForm(country=country, initial={'assigned_to': ticket.assigned_to})
    close_form = CloseTicketForm()

    if request.method == 'POST' and is_open:
        action = request.POST.get('action')
        opener = getattr(ticket.opened_by, 'technician', None)
        # Whoever didn't write it hears about it: the assignee, or the
        # opener when the assignee is the one acting.
        other = opener if technician == ticket.assigned_to else ticket.assigned_to
        if action == 'add_note':
            note_form = InternalTicketNoteForm(request.POST)
            if note_form.is_valid():
                TicketInternalNote.objects.create(
                    ticket=ticket, author=request.user, message=note_form.cleaned_data['message'],
                    created_at=timezone.now(),
                )
                if other and other != technician:
                    _push_internal_ticket(other, ticket, _('New note on an internal task'))
                return redirect('tasks:internal_ticket', pk=ticket.pk)
        elif action == 'reassign':
            reassign_form = ReassignInternalTicketForm(request.POST, country=country)
            if reassign_form.is_valid():
                ticket.assigned_to = reassign_form.cleaned_data['assigned_to']
                ticket.assigned_at = timezone.now()
                ticket.save(update_fields=['assigned_to', 'assigned_at'])
                TicketInternalNote.objects.create(
                    ticket=ticket, author=request.user, created_at=timezone.now(),
                    message=_('Reassigned to %(name)s.') % {'name': ticket.assigned_to.full_name},
                )
                if ticket.assigned_to != technician:
                    _push_internal_ticket(ticket.assigned_to, ticket, _('New internal task assigned to you'))
                messages.success(request, _('Reassigned successfully.'))
                return redirect('tasks:internal_ticket', pk=ticket.pk)
        elif action == 'close':
            close_form = CloseTicketForm(request.POST)
            if close_form.is_valid():
                ticket.status = CustomerTicket.Status.CLOSED
                ticket.close_reason = close_form.cleaned_data['close_reason']
                ticket.reviewed_by = request.user
                ticket.reviewed_at = timezone.now()
                ticket.save(update_fields=['status', 'close_reason', 'reviewed_by', 'reviewed_at'])
                if other and other != technician:
                    _push_internal_ticket(other, ticket, _('Internal task closed'))
                messages.success(request, _('Ticket closed.'))
                return redirect('tasks:internal_ticket', pk=ticket.pk)

    return render(request, 'tasks/internal_ticket.html', {
        'ticket': ticket,
        'is_open': is_open,
        'notes': ticket.internal_notes.select_related('author__technician').order_by('created_at'),
        'attachments': ticket.attachments.all(),
        'note_form': note_form,
        'reassign_form': reassign_form,
        'close_form': close_form,
        'can_convert': is_open and technician.is_any_manager,
    })
