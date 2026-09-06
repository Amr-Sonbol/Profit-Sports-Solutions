from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Case, IntegerField, Prefetch, Q, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _

from people.models import Technician, TechnicianSkill

from .forms import AddHelperForm, RemoveAssignmentForm, SetLeadForm, TaskCreateForm
from .models import Task, TaskAssignment, TaskEvent

TASK_NUMBER_CREATE_ATTEMPTS = 5

OPEN_STATUSES = [
    Task.Status.NEW,
    Task.Status.ASSIGNED,
    Task.Status.ACCEPTED,
    Task.Status.IN_PROGRESS,
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


def _require_supervisor(request):
    """Supervisor screens are restricted to supervisors/managers (see task_list)."""
    technician = getattr(request.user, 'technician', None)
    if technician is None or technician.role not in (Technician.Role.SUPERVISOR, Technician.Role.MANAGER):
        raise PermissionDenied
    return technician


@login_required
def task_list(request):
    _require_supervisor(request)

    status = request.GET.get('status', 'open')
    search = request.GET.get('q', '').strip()

    tasks = Task.objects.select_related('site__customer', 'task_type').prefetch_related(
        Prefetch(
            'assignments',
            queryset=TaskAssignment.objects.filter(
                role=TaskAssignment.Role.LEAD, is_active=True,
            ).select_related('technician'),
            to_attr='lead_assignments',
        ),
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

    tasks = tasks.annotate(priority_rank=PRIORITY_RANK).order_by('priority_rank', 'promised_at')

    paginator = Paginator(tasks, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    for task in page_obj:
        leads = task.lead_assignments
        task.lead_technician = leads[0].technician if leads else None

    context = {
        'page_obj': page_obj,
        'status': status,
        'search': search,
        'status_choices': Task.Status.choices,
    }
    return render(request, 'tasks/task_list.html', context)


@login_required
def task_detail(request, pk):
    _require_supervisor(request)

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
    _require_supervisor(request)

    if request.method == 'POST':
        form = TaskCreateForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
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
    _require_supervisor(request)

    task = get_object_or_404(Task.objects.select_related('site__customer__country'), pk=pk)
    active_assignments = list(task.assignments.filter(is_active=True).select_related('technician'))
    active_lead = next((a for a in active_assignments if a.role == TaskAssignment.Role.LEAD), None)
    active_helpers = [a for a in active_assignments if a.role == TaskAssignment.Role.HELPER]
    assigned_ids = {a.technician_id for a in active_assignments}

    locked = task.status in ASSIGNMENT_LOCKED_STATUSES
    candidates_qs = _assignment_candidates(task, exclude_ids=assigned_ids)

    set_lead_form = SetLeadForm(technicians=candidates_qs, requires_reason=bool(active_lead))
    add_helper_form = AddHelperForm(technicians=candidates_qs) if active_lead else None

    if request.method == 'POST' and not locked:
        action = request.POST.get('action')

        if action == 'set_lead':
            set_lead_form = SetLeadForm(request.POST, technicians=candidates_qs, requires_reason=bool(active_lead))
            if set_lead_form.is_valid():
                _set_lead(
                    task, active_lead, set_lead_form.cleaned_data['technician'],
                    set_lead_form.cleaned_data.get('end_reason', ''), request.user,
                )
                messages.success(request, _('Lead technician set.'))
                return redirect('tasks:task_assign', pk=task.pk)

        elif action == 'add_helper' and active_lead:
            add_helper_form = AddHelperForm(request.POST, technicians=candidates_qs)
            if add_helper_form.is_valid():
                TaskAssignment.objects.create(
                    task=task, technician=add_helper_form.cleaned_data['technician'],
                    role=TaskAssignment.Role.HELPER, assigned_at=timezone.now(), is_active=True,
                )
                messages.success(request, _('Helper added.'))
                return redirect('tasks:task_assign', pk=task.pk)

        elif action == 'remove_helper':
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
        'candidates': _candidates_with_skill_level(candidates_qs, task),
        'locked': locked,
        'set_lead_form': set_lead_form,
        'add_helper_form': add_helper_form,
        'remove_form': RemoveAssignmentForm(),
    }
    return render(request, 'tasks/task_assign.html', context)
