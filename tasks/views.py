from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Case, IntegerField, Prefetch, Q, Value, When
from django.forms import formset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _

from customers.models import Asset, Site
from people.models import Technician, TechnicianSkill
from people.permissions import require_supervisor, require_technician
from reference.models import Brand, Skill, TaskType
from reports.forms import PartUsedItemForm, WorkReportForm
from reports.models import PartUsed

from .forms import (
    AddHelperForm, BlockTaskForm, ExistingAssetOutcomeForm, MarkUnavailableForm, NewAssetForm,
    RemoveAssignmentForm, SetLeadForm, TaskAttachmentUploadForm, TaskCreateForm,
)
from .models import Task, TaskAsset, TaskAssignment, TaskAttachment, TaskEvent

TASK_NUMBER_CREATE_ATTEMPTS = 5
WEEK_LENGTH = 7

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
# task.status — only "start" and "complete" do; the step in between is read
# from which events already exist (see _next_technician_action).
TECHNICIAN_ACTIONS = {
    'accept': (TaskEvent.EventType.ACCEPTED, Task.Status.ACCEPTED),
    'en_route': (TaskEvent.EventType.EN_ROUTE, None),
    'arrive': (TaskEvent.EventType.ARRIVED, None),
    'start': (TaskEvent.EventType.STARTED, Task.Status.IN_PROGRESS),
    'complete': (TaskEvent.EventType.COMPLETED, Task.Status.COMPLETED),
}
BLOCKABLE_STATUSES = {Task.Status.ACCEPTED, Task.Status.IN_PROGRESS}

# A report can only be filed once work is underway or done.
REPORT_EDITABLE_STATUSES = {Task.Status.IN_PROGRESS, Task.Status.COMPLETED, Task.Status.CLOSED}
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
    prefix = f'{country.iso_code}-'
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
    if task.status == Task.Status.IN_PROGRESS:
        return 'complete'
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


@login_required
def task_list(request):
    require_supervisor(request)

    status = request.GET.get('status', 'open')
    search = request.GET.get('q', '').strip()

    tasks = _with_lead_prefetch(Task.objects.select_related('site__customer', 'task_type'))

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

    tasks = tasks.annotate(priority_rank=PRIORITY_RANK).order_by('priority_rank', 'promised_at')

    paginator = Paginator(tasks, 25)
    page_obj = paginator.get_page(request.GET.get('page'))
    _attach_lead_technician(page_obj)

    context = {
        'page_obj': page_obj,
        'status': status,
        'search': search,
        'status_choices': Task.Status.choices,
    }
    return render(request, 'tasks/task_list.html', context)


@login_required
def task_detail(request, pk):
    require_supervisor(request)

    task = get_object_or_404(
        Task.objects.select_related(
            'site__customer', 'task_type', 'brand', 'required_skill', 'created_by', 'report',
        ).prefetch_related('report__parts_used'),
        pk=pk,
    )
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
    }
    return render(request, 'tasks/task_detail.html', context)


@login_required
def task_create(request):
    require_supervisor(request)

    if request.method == 'POST':
        form = TaskCreateForm(request.POST)
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
                _save_new_task(task)
                TaskEvent.objects.create(
                    task=task, event_type=TaskEvent.EventType.CREATED,
                    occurred_at=timezone.now(), actor=request.user,
                )
            messages.success(request, _('Task %(number)s created.') % {'number': task.task_number})
            return redirect('tasks:task_detail', pk=task.pk)
    else:
        form = TaskCreateForm()

    return render(request, 'tasks/task_create.html', {'form': form})


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
    require_supervisor(request)

    task = get_object_or_404(Task.objects.select_related('site__customer__country'), pk=pk)
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
    require_supervisor(request)

    today, start, end = _week_window(request)

    scheduled = _with_lead_prefetch(
        Task.objects.filter(scheduled_for__date__range=(start, end))
        .select_related('site__customer', 'task_type').order_by('scheduled_for'),
    )
    _attach_lead_technician(scheduled)

    days = [{'date': start + timedelta(days=offset), 'tasks': []} for offset in range(WEEK_LENGTH)]
    tasks_by_date = {day['date']: day['tasks'] for day in days}
    for task in scheduled:
        tasks_by_date[timezone.localtime(task.scheduled_for).date()].append(task)

    unscheduled = _with_lead_prefetch(
        Task.objects.filter(scheduled_for__isnull=True, status__in=OPEN_STATUSES)
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
def my_week(request):
    technician = require_technician(request)

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

    context = {
        'days': days,
        'unscheduled': unscheduled,
        **_week_nav_context(today, start),
    }
    return render(request, 'tasks/my_week.html', context)


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


def _report_can_edit(report):
    """No report yet, or one that was sent back and hasn't been resubmitted."""
    return report is None or (report.approved_at is None and bool(report.rejection_reason))


@login_required
def my_report_form(request, pk):
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

    can_edit = _report_can_edit(report)
    task_assets = task.task_assets.select_related('asset__brand')
    parts = report.parts_used.all() if report else PartUsed.objects.none()

    if not can_edit:
        context = {
            'task': task, 'report': report, 'can_edit': False,
            'task_assets': task_assets, 'parts': parts,
        }
        return render(request, 'tasks/my_report_form.html', context)

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
                saved_report.rejection_reason = ''
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

                TaskEvent.objects.create(
                    task=task, event_type=TaskEvent.EventType.REPORT_SUBMITTED,
                    occurred_at=timezone.now(), actor=request.user,
                )
            messages.success(request, _('Report submitted.'))
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
