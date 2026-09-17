from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from people.models import RolePermission
from people.permissions import get_active_country, require_permission
from tasks.models import Task, TaskAssignment, TaskEvent

from .forms import CustomerFeedbackForm, RejectReportForm
from .models import CustomerFeedback, WorkReport


def _visit_date(task):
    """The report's submission date, in the task's own country — not UTC,
    per CLAUDE.md. Close enough to the actual visit: the technician files
    the report right after finishing the job.
    """
    country_tz = ZoneInfo(task.site.customer.country.timezone)
    return timezone.localtime(task.report.submitted_at, country_tz).date()


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
    link = request.build_absolute_uri(reverse('reports:feedback_form', args=[feedback.token]))
    with translation.override('en'):
        visit_date = _visit_date(task).strftime('%B %d, %Y')
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
def report_list(request):
    require_permission(request, RolePermission.Permission.REVIEW_REPORTS)

    status = request.GET.get('status', 'pending')
    reports = WorkReport.objects.filter(
        task__site__customer__country=get_active_country(request),
    ).select_related('task__site__customer')

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
    require_permission(request, RolePermission.Permission.REVIEW_REPORTS)

    report = get_object_or_404(
        WorkReport.objects.select_related(
            'task__site__customer', 'task__task_type',
        ).prefetch_related('parts_used'),
        pk=pk, task__site__customer__country=get_active_country(request),
    )
    task = report.task
    reviewed = report.approved_at is not None

    assignments = task.assignments.select_related('technician')
    active_lead = next(
        (a for a in assignments if a.role == TaskAssignment.Role.LEAD and a.is_active), None,
    )
    active_helpers = [a for a in assignments if a.role == TaskAssignment.Role.HELPER and a.is_active]

    reject_form = RejectReportForm(initial={'rejection_reason': report.rejection_reason})
    feedback = getattr(task, 'feedback', None)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'approve' and not reviewed:
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

        elif action == 'reject' and not reviewed:
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

        elif action == 'send_feedback_request' and reviewed:
            if not task.site.contact_email:
                messages.error(request, _('Add a contact email for this site before requesting feedback.'))
                return redirect('reports:report_review', pk=report.pk)
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
            return redirect('reports:report_review', pk=report.pk)

    context = {
        'report': report,
        'task': task,
        'lead_technician': active_lead.technician if active_lead else None,
        'helper_technicians': [a.technician for a in active_helpers],
        'task_assets': task.task_assets.select_related('asset__brand'),
        'attachments': task.attachments.select_related('uploaded_by'),
        'reviewed': reviewed,
        'reject_form': reject_form,
        'feedback': feedback,
    }
    return render(request, 'reports/report_review.html', context)


def feedback_form(request, token):
    """Public — no login. A customer rates their service through the link
    sent after their report is approved; there's no other way in.
    """
    feedback = get_object_or_404(
        CustomerFeedback.objects.select_related('task__report', 'task__site__customer__country'), token=token,
    )
    already_submitted = feedback.submitted_at is not None

    if request.method == 'POST' and not already_submitted:
        form = CustomerFeedbackForm(request.POST)
        if form.is_valid():
            feedback.rating = form.cleaned_data['rating']
            feedback.comment = form.cleaned_data['comment']
            feedback.submitted_at = timezone.now()
            feedback.save(update_fields=['rating', 'comment', 'submitted_at'])
            return redirect('reports:feedback_form', token=token)
    else:
        form = CustomerFeedbackForm()

    context = {
        'feedback': feedback, 'task': feedback.task, 'form': form,
        'visit_date': _visit_date(feedback.task),
    }
    return render(request, 'reports/feedback_form.html', context)
