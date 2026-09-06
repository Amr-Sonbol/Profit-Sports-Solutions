from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _

from people.permissions import require_supervisor
from tasks.models import Task, TaskAssignment, TaskEvent

from .forms import RejectReportForm
from .models import WorkReport


@login_required
def report_list(request):
    require_supervisor(request)

    status = request.GET.get('status', 'pending')
    reports = WorkReport.objects.select_related('task__site__customer')

    if status == 'pending':
        reports = reports.filter(approved_at__isnull=True, rejection_reason='')
    elif status == 'rejected':
        reports = reports.filter(approved_at__isnull=True).exclude(rejection_reason='')
    elif status == 'approved':
        reports = reports.filter(approved_at__isnull=False)
    # status == 'all': no filter

    reports = reports.order_by('submitted_at')

    context = {
        'reports': reports,
        'status': status,
    }
    return render(request, 'reports/report_list.html', context)


@login_required
def report_review(request, pk):
    require_supervisor(request)

    report = get_object_or_404(
        WorkReport.objects.select_related(
            'task__site__customer', 'task__task_type',
        ).prefetch_related('parts_used'),
        pk=pk,
    )
    task = report.task
    reviewed = report.approved_at is not None

    assignments = task.assignments.select_related('technician')
    active_lead = next(
        (a for a in assignments if a.role == TaskAssignment.Role.LEAD and a.is_active), None,
    )

    reject_form = RejectReportForm(initial={'rejection_reason': report.rejection_reason})

    if request.method == 'POST' and not reviewed:
        action = request.POST.get('action')

        if action == 'approve':
            report.approved_at = timezone.now()
            report.save(update_fields=['approved_at'])
            task.status = Task.Status.CLOSED
            task.save(update_fields=['status'])
            TaskEvent.objects.create(
                task=task, event_type=TaskEvent.EventType.REPORT_APPROVED,
                occurred_at=timezone.now(), actor=request.user,
            )
            messages.success(request, _('Report approved.'))
            return redirect('reports:report_review', pk=report.pk)

        elif action == 'reject':
            reject_form = RejectReportForm(request.POST)
            if reject_form.is_valid():
                report.rejection_reason = reject_form.cleaned_data['rejection_reason']
                report.save(update_fields=['rejection_reason'])
                TaskEvent.objects.create(
                    task=task, event_type=TaskEvent.EventType.REPORT_REJECTED,
                    occurred_at=timezone.now(), actor=request.user,
                )
                messages.success(request, _('Report sent back to the technician.'))
                return redirect('reports:report_review', pk=report.pk)

    context = {
        'report': report,
        'task': task,
        'lead_technician': active_lead.technician if active_lead else None,
        'attachments': task.attachments.select_related('uploaded_by'),
        'reviewed': reviewed,
        'reject_form': reject_form,
    }
    return render(request, 'reports/report_review.html', context)
