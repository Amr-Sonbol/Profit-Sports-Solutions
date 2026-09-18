from datetime import timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, IntegerField, Prefetch, Q, Sum, Value, When
from django.forms import formset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from customers.models import Asset, Customer, Site
from people.models import (
    RELIABLE_LEVEL, NotificationSettings, RolePermission, Technician, TechnicianConduct,
    TechnicianConductAssessment, TechnicianSkill, TechnicianSkillAssessment,
)
from people.permissions import (
    ACTIVE_COUNTRY_SESSION_KEY, get_active_country, require_manager, require_permission, require_technician,
)
from reference.models import Brand, ConductArea, Country, Skill, TaskType
from reports.forms import PartUsedItemForm, WorkReportForm
from reports.models import CustomerFeedback, PartUsed

from .forms import (
    AddHelperForm, AssignTicketForm, BlockTaskForm, CustomerTicketForm, DismissTicketForm,
    ExistingAssetOutcomeForm, MarkUnavailableForm, MyProfileForm, NewAssetForm, RemoveAssignmentForm,
    ReviewLevelForm, SelfRateLevelForm, SetLeadForm, TaskAttachmentUploadForm, TaskCreateForm,
    TaskEditForm, TechnicianCreateForm, TechnicianPhotoForm, TicketLogisticsForm,
)
from .models import (
    CustomerTicket, CustomerTicketAttachment, Task, TaskAsset, TaskAssignment, TaskAttachment, TaskEvent,
)

TASK_NUMBER_CREATE_ATTEMPTS = 5
WEEK_LENGTH = 7

# A new technician works as helper, alongside a supervisor, until
# certified — the target is to get there within this many days.
NINETY_DAY_TRACK_DAYS = 90

OPEN_STATUSES = [
    Task.Status.NEW,
    Task.Status.ASSIGNED,
    Task.Status.ACCEPTED,
    Task.Status.IN_PROGRESS,
    Task.Status.COMPLETED,
    Task.Status.BLOCKED,
]

# "Blocked once work starts. After in_progress, handover means closing the
# task and raising a new one" (docs/database_design_v2.md, task_assignment).
ASSIGNMENT_LOCKED_STATUSES = {
    Task.Status.IN_PROGRESS,
    Task.Status.COMPLETED,
    Task.Status.CLOSED,
    Task.Status.CANCELLED,
}

# Once a lead is (re)assigned, an in-progress task pipeline restarts at
# "assigned" — the new lead has not accepted yet. Statuses outside this set
# (e.g. blocked) are left alone.
STATUSES_RESET_BY_ASSIGNMENT = {Task.Status.NEW, Task.Status.ASSIGNED, Task.Status.ACCEPTED}

# The lead's button taps, in order. "en_route" and "arrived" don't move
# task.status — only "start" does; from there, filing the report is what
# closes the task (see my_report_form) — there's no separate "complete" tap
# or supervisor approval step anymore.
TECHNICIAN_ACTIONS = {
    'accept': (TaskEvent.EventType.ACCEPTED, Task.Status.ACCEPTED),
    'en_route': (TaskEvent.EventType.EN_ROUTE, None),
    'arrive': (TaskEvent.EventType.ARRIVED, None),
    'start': (TaskEvent.EventType.STARTED, Task.Status.IN_PROGRESS),
}
BLOCKABLE_STATUSES = {Task.Status.ACCEPTED, Task.Status.IN_PROGRESS}

# A report can only be filed once work is underway, or re-edited after the
# fact (closing again just re-saves it — see _report_can_edit).
REPORT_EDITABLE_STATUSES = {Task.Status.IN_PROGRESS, Task.Status.CLOSED}
EXISTING_ASSET_ROWS = 4
NEW_ASSET_ROWS = 4
PART_ROWS = 5

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


def _skills_with_current_rating(technician):
    """Every active skill, each annotated with `.current` — the
    technician's TechnicianSkill row for it, or None if never rated.
    """
    skills = list(Skill.objects.filter(is_active=True).select_related('brand').order_by('brand__name', 'name'))
    current_by_skill_id = {
        rating.skill_id: rating
        for rating in TechnicianSkill.objects.filter(
            technician=technician, skill__in=skills,
        ).select_related('set_by')
    }
    for skill in skills:
        skill.current = current_by_skill_id.get(skill.id)
    return skills


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
    on its own (docs/database_design_v2.md, §3). Cardio skills are excluded
    from the bar; clearing one instead marks readiness for the supervisor
    track.
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


def _attachment_media_type(uploaded_file):
    content_type = uploaded_file.content_type or ''
    if content_type.startswith('image/'):
        return TaskAttachment.MediaType.PHOTO
    if content_type.startswith('video/'):
        return TaskAttachment.MediaType.VIDEO
    return TaskAttachment.MediaType.DOCUMENT


def _save_attachment(request, task, uploaded_file, purpose):
    path = default_storage.save(f'attachments/{task.pk}/{uploaded_file.name}', uploaded_file)
    TaskAttachment.objects.create(
        task=task, storage_kind=TaskAttachment.StorageKind.FILE,
        url=request.build_absolute_uri(default_storage.url(path)),
        media_type=_attachment_media_type(uploaded_file), purpose=purpose,
        source=TaskAttachment.Source.TECHNICIAN, uploaded_by=request.user, uploaded_at=timezone.now(),
    )


def _send_schedule_notification(task):
    """Best-effort, and forced to English — same reasoning as
    reports._send_feedback_email: a failed send shouldn't block the
    supervisor's flow, and there's nowhere to read a customer's language
    preference from. Manual every time; nothing calls this on its own.
    """
    country_tz = ZoneInfo(task.site.customer.country.timezone)
    local_time = timezone.localtime(task.scheduled_for, country_tz)
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
        ) % {
            'contact': task.site.contact_name or task.site.customer.name,
            'site': task.site.name,
            'date': local_time.strftime('%B %d, %Y'),
            'time': local_time.strftime('%I:%M %p').lstrip('0'),
        }
    send_mail(
        subject=subject, message=message, from_email=None,
        recipient_list=[task.site.contact_email], fail_silently=True,
    )


def _send_delay_notice(task, reason):
    """A different message from _send_schedule_notification: that one
    confirms a plan, this one apologizes for one slipping — traffic, a
    previous job running long, and so on. Always manual and always
    needs a reason, since there's no automatic way to know the team is
    running behind.
    """
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
        ) % {
            'contact': task.site.contact_name or task.site.customer.name,
            'site': task.site.name,
            'reason': reason,
        }
    send_mail(
        subject=subject, message=message, from_email=None,
        recipient_list=[task.site.contact_email], fail_silently=True,
    )


def _send_shipping_notice(contact_name, entity_name, reference, tracking_number, contact_email):
    """Shared by task detail and ticket review — the only two places a
    shipping_tracking_number can live. Only the tracking number goes to
    the customer; pak_reference_number is internal and never sent.
    """
    with translation.override('en'):
        subject = _('Your part(s) have shipped — %(reference)s') % {'reference': reference}
        message = _(
            'Dear %(contact)s,\n\n'
            'The part(s) for %(entity)s are on their way. Tracking number: %(tracking)s\n\n'
            'Best regards,\n'
            'Profit Sports Solutions\n',
        ) % {'contact': contact_name, 'entity': entity_name, 'tracking': tracking_number}
    send_mail(
        subject=subject, message=message, from_email=None,
        recipient_list=[contact_email], fail_silently=True,
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
        ) % {
            'contact': task.site.contact_name or task.site.customer.name,
            'site': task.site.name,
            'date': visit_date,
            'link': link,
        }
    send_mail(
        subject=subject, message=message, from_email=None,
        recipient_list=[task.site.contact_email],
        fail_silently=True,
    )


@login_required
def dashboard(request):
    """At a glance: who's available today, and every open task with its
    lead and schedule. The landing page stays task_list — this is an
    additional screen, not a replacement.
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
    search = request.GET.get('q', '').strip()
    customer_id = request.GET.get('customer', '')
    technician_id = request.GET.get('technician', '')
    scheduled_from = parse_date(request.GET.get('scheduled_from', '') or '')
    scheduled_to = parse_date(request.GET.get('scheduled_to', '') or '')

    tasks = _with_lead_prefetch(
        Task.objects.filter(site__customer__country=active_country).select_related('site__customer', 'task_type'),
    )

    if status == 'open':
        tasks = tasks.filter(status__in=OPEN_STATUSES)
    elif status != 'all':
        tasks = tasks.filter(status=status)

    if search:
        tasks = tasks.filter(
            Q(task_number__icontains=search)
            | Q(site__name__icontains=search)
            | Q(site__customer__name__icontains=search)
        )

    if customer_id:
        tasks = tasks.filter(site__customer_id=customer_id)

    if technician_id:
        tasks = tasks.filter(
            assignments__technician_id=technician_id, assignments__is_active=True,
        ).distinct()

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
            'q': search, 'customer': customer_id, 'technician': technician_id,
            'scheduled_from': request.GET.get('scheduled_from', ''),
            'scheduled_to': request.GET.get('scheduled_to', ''),
        }.items() if value
    }

    context = {
        'page_obj': page_obj,
        'status': status,
        'search': search,
        'status_choices': Task.Status.choices,
        'customers': Customer.objects.filter(is_active=True, country=active_country).order_by('name'),
        'technicians': Technician.objects.filter(is_active=True, country=active_country).order_by('full_name'),
        'selected_customer': customer_id,
        'selected_technician': technician_id,
        'scheduled_from': request.GET.get('scheduled_from', ''),
        'scheduled_to': request.GET.get('scheduled_to', ''),
        'filter_qs': urlencode(filter_params),
    }
    return render(request, 'tasks/task_list.html', context)


@login_required
def task_detail(request, pk):
    require_permission(request, RolePermission.Permission.VIEW_TASKS)

    task = get_object_or_404(
        Task.objects.select_related(
            'site__customer__country', 'task_type', 'brand', 'required_skill', 'created_by', 'report',
        ).prefetch_related('report__parts_used'),
        pk=pk, site__customer__country=get_active_country(request),
    )

    if request.method == 'POST' and request.POST.get('action') == 'notify_schedule':
        require_permission(request, RolePermission.Permission.CREATE_TASKS)
        _require_task_owner(request, task)
        if not task.scheduled_for:
            messages.error(request, _('Set a scheduled time before notifying the customer.'))
        elif not task.site.contact_email:
            messages.error(request, _('Add a contact email for this site before notifying the customer.'))
        else:
            _send_schedule_notification(task)
            task.schedule_notified_at = timezone.now()
            task.schedule_notified_by = request.user
            task.save(update_fields=['schedule_notified_at', 'schedule_notified_by'])
            messages.success(request, _('Customer notified of the scheduled visit.'))
        return redirect('tasks:task_detail', pk=task.pk)

    if request.method == 'POST' and request.POST.get('action') == 'notify_delay':
        require_permission(request, RolePermission.Permission.CREATE_TASKS)
        _require_task_owner(request, task)
        reason = request.POST.get('delay_reason', '').strip()
        if not task.scheduled_for:
            messages.error(request, _('Set a scheduled time before notifying the customer.'))
        elif not task.site.contact_email:
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
        elif not task.site.contact_email:
            messages.error(request, _('Add a contact email for this site before notifying the customer.'))
        else:
            _send_shipping_notice(
                task.site.contact_name or task.site.customer.name, task.site.name,
                task.task_number, task.shipping_tracking_number, task.site.contact_email,
            )
            messages.success(request, _('Customer notified of the tracking number.'))
        return redirect('tasks:task_detail', pk=task.pk)

    if request.method == 'POST' and request.POST.get('action') == 'send_feedback_request':
        require_permission(request, RolePermission.Permission.CREATE_TASKS)
        _require_task_owner(request, task)
        feedback = getattr(task, 'feedback', None)
        if task.status != Task.Status.CLOSED:
            messages.error(request, _('Close the task (file its report) before requesting feedback.'))
        elif not task.site.contact_email:
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

    context = {
        'task': task,
        'active_lead': active_lead,
        'active_helpers': active_helpers,
        'events': task.events.select_related('actor', 'corrected_by'),
        'attachments': task.attachments.select_related('uploaded_by'),
        'task_assets': task.task_assets.select_related('asset'),
        'report': getattr(task, 'report', None),
        'feedback': getattr(task, 'feedback', None),
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
    require_permission(request, RolePermission.Permission.CREATE_TASKS)
    active_country = get_active_country(request)
    task = get_object_or_404(Task, pk=pk, site__customer__country=active_country)
    _require_task_owner(request, task)
    previous_scheduled_for = task.scheduled_for

    if request.method == 'POST':
        form = TaskEditForm(request.POST, instance=task, country=active_country)
        if form.is_valid():
            with transaction.atomic():
                updated_task = form.save()
                rescheduled = updated_task.scheduled_for != previous_scheduled_for
                if rescheduled:
                    TaskEvent.objects.create(
                        task=updated_task, event_type=TaskEvent.EventType.RESCHEDULED,
                        occurred_at=timezone.now(), actor=request.user,
                    )
            if (
                rescheduled and updated_task.scheduled_for and updated_task.site.contact_email
                and NotificationSettings.load().auto_notify_on_reschedule
            ):
                _send_schedule_notification(updated_task)
                updated_task.schedule_notified_at = timezone.now()
                updated_task.schedule_notified_by = request.user
                updated_task.save(update_fields=['schedule_notified_at', 'schedule_notified_by'])
                messages.success(request, _('Task updated. Customer notified automatically.'))
            else:
                messages.success(request, _('Task updated.'))
            return redirect('tasks:task_detail', pk=task.pk)
    else:
        form = TaskEditForm(instance=task, country=active_country)

    return render(request, 'tasks/task_edit.html', {'task': task, 'form': form})


@login_required
def task_create(request):
    require_permission(request, RolePermission.Permission.CREATE_TASKS)
    active_country = get_active_country(request)

    ticket = None
    ticket_id = request.GET.get('ticket')
    if ticket_id:
        ticket = get_object_or_404(
            CustomerTicket, pk=ticket_id, status=CustomerTicket.Status.NEW, country=active_country,
        )

    if request.method == 'POST':
        form = TaskCreateForm(request.POST, country=active_country, ticket=ticket)
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

                required_skill = cleaned.get('required_skill')
                if not required_skill and cleaned.get('new_skill_wanted') and brand:
                    required_skill, _created = Skill.objects.get_or_create(brand=brand, name=brand.name)

                task = form.save(commit=False)
                task.site = site
                task.brand = brand
                task.task_type = task_type
                task.required_skill = required_skill
                task.created_by = request.user
                task.status = Task.Status.NEW
                if ticket is not None:
                    task.pak_reference_number = ticket.pak_reference_number
                    task.shipping_tracking_number = ticket.shipping_tracking_number
                _save_new_task(task)
                TaskEvent.objects.create(
                    task=task, event_type=TaskEvent.EventType.CREATED,
                    occurred_at=timezone.now(), actor=request.user,
                )
                if ticket is not None:
                    ticket.task = task
                    ticket.status = CustomerTicket.Status.CONVERTED
                    ticket.reviewed_by = request.user
                    ticket.reviewed_at = timezone.now()
                    ticket.save(update_fields=['task', 'status', 'reviewed_by', 'reviewed_at'])
            messages.success(request, _('Task %(number)s created.') % {'number': task.task_number})
            return redirect('tasks:task_detail', pk=task.pk)
    else:
        form = TaskCreateForm(country=active_country, ticket=ticket)

    return render(request, 'tasks/task_create.html', {'form': form, 'ticket': ticket})


def ticket_form(request):
    """Public — no login. A customer describing a complaint or request in
    their own words, self-identified rather than matched to a real site.
    """
    if request.method == 'POST':
        form = CustomerTicketForm(request.POST, request.FILES)
        if form.is_valid():
            with transaction.atomic():
                ticket = form.save(commit=False)
                ticket.submitted_at = timezone.now()
                ticket.save()
                for uploaded_file in form.cleaned_data['attachments']:
                    CustomerTicketAttachment.objects.create(
                        ticket=ticket, file=uploaded_file, uploaded_at=timezone.now(),
                    )
            return redirect('tasks:ticket_submitted')
    else:
        form = CustomerTicketForm()

    return render(request, 'tasks/ticket_form.html', {'form': form})


def ticket_submitted(request):
    """Public — the thank-you page, split from ticket_form so refreshing
    it doesn't risk resubmitting the form.
    """
    return render(request, 'tasks/ticket_submitted.html')


@login_required
def ticket_list(request):
    """Every customer-submitted ticket still needing a decision, plus
    what's already been resolved — country-scoped like everything else a
    supervisor reviews.
    """
    require_permission(request, RolePermission.Permission.MANAGE_TICKETS)

    status = request.GET.get('status', 'new')
    tickets = CustomerTicket.objects.filter(country=get_active_country(request)).select_related('assigned_to')
    if status != 'all':
        tickets = tickets.filter(status=status)
    tickets = tickets.order_by('-submitted_at')

    context = {'tickets': tickets, 'status': status, 'status_choices': CustomerTicket.Status.choices}
    return render(request, 'tasks/ticket_list.html', context)


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
    # only offers same-country technicians), so this costs it nothing.
    ticket = get_object_or_404(CustomerTicket, pk=pk, country=get_active_country(request))

    can_manage = RolePermission.objects.filter(
        role=technician.role, permission=RolePermission.Permission.MANAGE_TICKETS, allowed=True,
    ).exists()
    is_assignee = ticket.assigned_to_id == technician.id
    if not (can_manage or is_assignee):
        raise PermissionDenied

    dismiss_form = DismissTicketForm()
    assign_form = AssignTicketForm(country=ticket.country, initial={'assigned_to': ticket.assigned_to_id})
    logistics_form = TicketLogisticsForm(instance=ticket)

    if request.method == 'POST' and can_manage:
        action = request.POST.get('action')

        if action == 'update_logistics':
            logistics_form = TicketLogisticsForm(request.POST, instance=ticket)
            if logistics_form.is_valid():
                logistics_form.save()
                messages.success(request, _('Logistics updated.'))
                return redirect('tasks:ticket_review', pk=ticket.pk)

        elif action == 'notify_shipping':
            if not ticket.shipping_tracking_number:
                messages.error(request, _('Add a shipping tracking number before notifying the customer.'))
            elif not ticket.contact_email:
                messages.error(request, _('Add a contact email before notifying the customer.'))
            else:
                _send_shipping_notice(
                    ticket.contact_name, ticket.site_description, f'ticket #{ticket.pk}',
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

        elif action == 'assign':
            assign_form = AssignTicketForm(request.POST, country=ticket.country)
            if assign_form.is_valid():
                ticket.assigned_to = assign_form.cleaned_data['assigned_to']
                ticket.assigned_at = timezone.now()
                ticket.save(update_fields=['assigned_to', 'assigned_at'])
                messages.success(request, _('Ticket assigned.'))
                return redirect('tasks:ticket_review', pk=ticket.pk)

    context = {
        'ticket': ticket, 'dismiss_form': dismiss_form, 'assign_form': assign_form,
        'logistics_form': logistics_form, 'can_manage': can_manage,
    }
    return render(request, 'tasks/ticket_review.html', context)


def _require_task_owner(request, task):
    """Once a task has a responsible supervisor, only they (or a manager)
    can edit it, manage its assignment, or act on it from its detail page.
    An unowned task (no responsible supervisor set yet) stays open to
    anyone the usual permission already let in — same as before this
    field existed, and how it gets claimed in the first place.
    """
    technician = request.user.technician
    if technician.role == Technician.Role.MANAGER:
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
    require_permission(request, RolePermission.Permission.ASSIGN_TASKS)

    task = get_object_or_404(
        Task.objects.select_related('site__customer__country'),
        pk=pk, site__customer__country=get_active_country(request),
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

        elif action == 'mark_unavailable':
            # A technician's availability isn't specific to this task, so
            # this isn't gated by `locked` the way assignment changes are.
            technician = get_object_or_404(
                Technician, pk=request.POST.get('technician_id'), country=task.site.customer.country,
            )
            mark_unavailable_form = MarkUnavailableForm(request.POST)
            if mark_unavailable_form.is_valid():
                technician.is_available = False
                technician.unavailable_reason = mark_unavailable_form.cleaned_data['reason']
                technician.save(update_fields=['is_available', 'unavailable_reason'])
                messages.success(request, _('Marked unavailable.'))
                return redirect('tasks:task_assign', pk=task.pk)

        elif action == 'mark_available':
            technician = get_object_or_404(
                Technician, pk=request.POST.get('technician_id'), country=task.site.customer.country,
            )
            technician.is_available = True
            technician.unavailable_reason = ''
            technician.save(update_fields=['is_available', 'unavailable_reason'])
            messages.success(request, _('Marked available.'))
            return redirect('tasks:task_assign', pk=task.pk)

    context = {
        'task': task,
        'active_lead': active_lead,
        'active_helpers': active_helpers,
        'candidates': _candidates_with_skill_level(candidates_qs, task),
        'locked': locked,
        'set_lead_form': set_lead_form,
        'add_helper_form': add_helper_form,
        'remove_form': RemoveAssignmentForm(),
        'mark_unavailable_form': MarkUnavailableForm(),
    }
    return render(request, 'tasks/task_assign.html', context)


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


def _week_board(technician, request):
    """(days, unscheduled, nav_context) for one technician's week — the
    start time and estimated finish (Task.estimated_finish) travel with
    each task automatically, since that's a model property. Shared by
    my_week (self) and technician_board (a supervisor viewing someone else).
    """
    today, start, end = _week_window(request)

    scheduled_assignments = TaskAssignment.objects.filter(
        technician=technician, is_active=True, task__scheduled_for__date__range=(start, end),
    ).select_related('task__site__customer', 'task__task_type').order_by('task__scheduled_for')

    days = [{'date': start + timedelta(days=offset), 'tasks': []} for offset in range(WEEK_LENGTH)]
    tasks_by_date = {day['date']: day['tasks'] for day in days}
    for assignment in scheduled_assignments:
        task = assignment.task
        task.my_role = assignment.role
        tasks_by_date[timezone.localtime(task.scheduled_for).date()].append(task)

    unscheduled_assignments = TaskAssignment.objects.filter(
        technician=technician, is_active=True, task__scheduled_for__isnull=True,
        task__status__in=OPEN_STATUSES,
    )
    role_by_task_id = {a.task_id: a.role for a in unscheduled_assignments}
    unscheduled = Task.objects.filter(pk__in=role_by_task_id).select_related(
        'site__customer', 'task_type',
    ).annotate(priority_rank=PRIORITY_RANK).order_by('priority_rank', 'promised_at')
    for task in unscheduled:
        task.my_role = role_by_task_id[task.pk]

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
    the current week. No computed on-time %/first-time-fix rates: per the
    doc's own build order, those need months of real event data to mean
    anything.
    """
    technician = require_technician(request)

    skills = _skills_with_current_rating(technician)
    conduct_areas = _conduct_areas_with_current_rating(technician)
    certification = _certification_status(technician)
    ninety_day = _ninety_day_progress(technician, certification)

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
        actor=request.user, event_type=TaskEvent.EventType.CLOSED, occurred_at__date__range=(start, end),
    ).count()

    context = {
        'skills': skills,
        'conduct_areas': conduct_areas,
        'certification': certification,
        'ninety_day': ninety_day,
        'rank': rank,
        'leaderboard_size': len(leaderboard),
        'assigned_count': assigned_count,
        'completed_count': completed_count,
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
            form = SelfRateLevelForm(request.POST)
            if not TechnicianSkill.objects.filter(technician=technician, skill=skill).exists() and form.is_valid():
                level = int(form.cleaned_data['level'])
                with transaction.atomic():
                    TechnicianSkill.objects.create(
                        technician=technician, skill=skill, level=level,
                        source=TechnicianSkill.Source.SELF, set_by=technician, set_on=today,
                    )
                    TechnicianSkillAssessment.objects.create(
                        technician=technician, skill=skill, level=level,
                        source=TechnicianSkill.Source.SELF, set_by=technician, set_on=today,
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

    context = {
        'skills': _skills_with_current_rating(technician),
        'conduct_areas': _conduct_areas_with_current_rating(technician),
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

    context = {
        'technician': technician,
        'profile_form': profile_form,
        'password_form': password_form,
        'certification': certification,
        'ninety_day': _ninety_day_progress(technician, certification),
        'rank': rank,
        'leaderboard_size': len(leaderboard),
    }
    return render(request, 'tasks/my_profile.html', context)


@login_required
def technician_create(request):
    require_permission(request, RolePermission.Permission.MANAGE_TECHNICIANS)

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
    """
    require_permission(request, RolePermission.Permission.VIEW_TECHNICIANS)

    search = request.GET.get('q', '').strip()
    technicians = Technician.objects.filter(
        is_active=True, country=get_active_country(request),
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

    return render(request, 'tasks/technician_list.html', {'rows': rows, 'search': search})


@login_required
def technician_board(request, pk):
    """A supervisor's view of one technician's week — same shape as
    my_week, just for someone else, with the same country scoping used
    everywhere else a supervisor looks at a specific technician.
    """
    require_permission(request, RolePermission.Permission.VIEW_TECHNICIANS)
    technician = get_object_or_404(Technician, pk=pk, country=get_active_country(request))

    days, unscheduled, nav_context = _week_board(technician, request)
    context = {'technician': technician, 'days': days, 'unscheduled': unscheduled, **nav_context}
    return render(request, 'tasks/technician_board.html', context)


@login_required
def technician_skills(request, pk):
    """A supervisor's review screen for one technician — confirm or
    override every self-rating against real evidence. A level only ever
    changes here, by a deliberate supervisor action; nothing computed
    writes to it automatically.
    """
    supervisor = require_permission(request, RolePermission.Permission.REVIEW_SKILLS)
    technician = get_object_or_404(Technician, pk=pk, country=get_active_country(request))

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

    context = {
        'technician': technician,
        'skills': _skills_with_current_rating(technician),
        'conduct_areas': _conduct_areas_with_current_rating(technician),
        'review_form': ReviewLevelForm(),
        'certification': _certification_status(technician),
    }
    return render(request, 'tasks/technician_skills.html', context)


@login_required
def technician_edit(request, pk):
    """Just the photo, for now — a supervisor/manager sets it from the
    roster, same country scoping as every other per-technician screen.
    """
    require_permission(request, RolePermission.Permission.MANAGE_TECHNICIANS)
    technician = get_object_or_404(Technician, pk=pk, country=get_active_country(request))

    if request.method == 'POST':
        form = TechnicianPhotoForm(request.POST, request.FILES, instance=technician)
        if form.is_valid():
            form.save()
            messages.success(request, _('Photo updated.'))
            return redirect('tasks:technician_list')
    else:
        form = TechnicianPhotoForm(instance=technician)

    return render(request, 'tasks/technician_edit.html', {'technician': technician, 'form': form})


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
        messages.success(request, _('Now viewing %(country)s.') % {'country': country.name})

    next_url = request.POST.get('next', '')
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = reverse('tasks:dashboard')
    return redirect(next_url)


@login_required
def role_permissions(request):
    """Manager-only: which role can do what, plus global notification
    behavior. Deliberately not gated by the configurable system it
    manages (require_manager, not require_permission) — otherwise a bad
    edit here could lock every role out of ever fixing it again.
    """
    require_manager(request)

    roles = [Technician.Role.TECHNICIAN, Technician.Role.SUPERVISOR, Technician.Role.MANAGER]
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
    can_block = is_lead and task.status in BLOCKABLE_STATUSES

    upload_form = TaskAttachmentUploadForm()
    block_form = BlockTaskForm()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action in TECHNICIAN_ACTIONS and action == next_action:
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

        elif action == 'upload':
            upload_form = TaskAttachmentUploadForm(request.POST, request.FILES)
            if upload_form.is_valid():
                _save_attachment(
                    request, task, upload_form.cleaned_data['file'], upload_form.cleaned_data['purpose'],
                )
                messages.success(request, _('Photo added.'))
                return redirect('tasks:my_task_detail', pk=task.pk)

    context = {
        'task': task,
        'is_lead': is_lead,
        'next_action': next_action,
        'can_block': can_block,
        'can_file_report': is_lead and task.status in REPORT_EDITABLE_STATUSES,
        'report': getattr(task, 'report', None),
        'attachments': task.attachments.select_related('uploaded_by'),
        'events': task.events.order_by('occurred_at'),
        'upload_form': upload_form,
        'block_form': block_form,
    }
    return render(request, 'tasks/my_task_detail.html', context)


@login_required
def my_report_form(request, pk):
    """Filing this report is the terminal step of the whole task — there's
    no separate "mark complete" tap and no supervisor approval gate.
    Saving it (first time or a later correction) closes the task, or
    re-confirms it as closed if it already was.
    """
    technician = require_technician(request)

    assignment = get_object_or_404(
        TaskAssignment.objects.select_related('task__site__customer__country'),
        task__pk=pk, technician=technician, is_active=True, role=TaskAssignment.Role.LEAD,
    )
    task = assignment.task
    report = getattr(task, 'report', None)

    if task.status not in REPORT_EDITABLE_STATUSES:
        messages.error(request, _('Start work on this task before filing a report.'))
        return redirect('tasks:my_task_detail', pk=task.pk)

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
        report_form = WorkReportForm(request.POST, request.FILES, instance=report)
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
                saved_report = report_form.save(commit=False)
                saved_report.task = task
                saved_report.submitted_at = timezone.now()
                signature = report_form.cleaned_data.get('signature')
                if signature:
                    path = default_storage.save(f'signatures/{task.pk}/{signature.name}', signature)
                    saved_report.signature_url = request.build_absolute_uri(default_storage.url(path))
                saved_report.save()

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

                saved_report.parts_used.all().delete()
                for cleaned in part_formset.cleaned_data:
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
                if task.status != Task.Status.CLOSED:
                    task.status = Task.Status.CLOSED
                    task.save(update_fields=['status'])
                    TaskEvent.objects.create(
                        task=task, event_type=TaskEvent.EventType.CLOSED,
                        occurred_at=now, actor=request.user,
                    )
            messages.success(request, _('Report submitted. Task closed.'))
            return redirect('tasks:my_task_detail', pk=task.pk)
    else:
        report_form = WorkReportForm(instance=report)
        existing_formset = ExistingAssetFormSet(
            initial=existing_initial, prefix='existing', form_kwargs={'site': task.site},
        )
        new_formset = NewAssetFormSet(prefix='new')
        part_formset = PartFormSet(initial=part_initial, prefix='parts')

    context = {
        'task': task,
        'report': report,
        'can_edit': True,
        'report_form': report_form,
        'existing_formset': existing_formset,
        'has_existing_assets': bool(existing_assets),
        'new_formset': new_formset,
        'part_formset': part_formset,
    }
    return render(request, 'tasks/my_report_form.html', context)
