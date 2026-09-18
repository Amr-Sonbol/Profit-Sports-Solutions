from zoneinfo import ZoneInfo

from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import CustomerFeedbackForm
from .models import CustomerFeedback


def _visit_date(task):
    """The report's submission date, in the task's own country — not UTC,
    per CLAUDE.md. Close enough to the actual visit: the technician files
    the report right after finishing the job.
    """
    country_tz = ZoneInfo(task.site.customer.country.timezone)
    return timezone.localtime(task.report.submitted_at, country_tz).date()


def feedback_form(request, token):
    """Public — no login. A customer rates their service through the link
    sent once their task is closed; there's no other way in.
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
