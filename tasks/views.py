from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Case, IntegerField, Prefetch, Q, Value, When
from django.shortcuts import render

from people.models import Technician

from .models import Task, TaskAssignment

OPEN_STATUSES = [
    Task.Status.NEW,
    Task.Status.ASSIGNED,
    Task.Status.ACCEPTED,
    Task.Status.IN_PROGRESS,
    Task.Status.BLOCKED,
]

PRIORITY_RANK = Case(
    When(priority=Task.Priority.EMERGENCY, then=Value(0)),
    When(priority=Task.Priority.HIGH, then=Value(1)),
    When(priority=Task.Priority.NORMAL, then=Value(2)),
    When(priority=Task.Priority.LOW, then=Value(3)),
    default=Value(4),
    output_field=IntegerField(),
)


@login_required
def task_list(request):
    technician = getattr(request.user, 'technician', None)
    if technician is None or technician.role not in (Technician.Role.SUPERVISOR, Technician.Role.MANAGER):
        raise PermissionDenied

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
