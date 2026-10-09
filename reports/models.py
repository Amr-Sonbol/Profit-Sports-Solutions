import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from tasks.models import Task, TaskEvent


# Labour hours this far past the task's estimate get flagged to
# supervisors — 1.5 means more than 50% over.
OVERRUN_RATIO = Decimal('1.5')

# Typed labour hours this far above the time the app's own taps show get
# flagged — both more than 25% and more than half an hour over, so a
# short job rounded up isn't flagged.
ABOVE_TAPPED_RATIO = Decimal('1.25')
ABOVE_TAPPED_SLACK = Decimal('0.5')


class WorkReport(models.Model):
    """The lead submits one report for the whole task; the customer signs once."""

    task = models.OneToOneField(
        Task, on_delete=models.CASCADE, related_name='report',
        verbose_name=_('task'),
    )
    findings = models.TextField(_('findings'), help_text=_('what the technician found'))
    action_taken = models.TextField(_('action taken'), blank=True)
    resolved = models.BooleanField(_('resolved'))
    labour_hours = models.DecimalField(_('labour hours'), max_digits=5, decimal_places=2)
    customer_name = models.CharField(_('customer name'), max_length=150, help_text=_('who signed'))
    signature_url = models.URLField(_('signature URL'), blank=True)
    signature_waived_reason = models.TextField(
        _('why there is no signature'), blank=True,
        help_text=_('filled in when the customer was not available to sign'),
    )
    submitted_at = models.DateTimeField(_('submitted at'))

    class Meta:
        verbose_name = _('work report')
        verbose_name_plural = _('work reports')
        ordering = ['-submitted_at']

    def __str__(self):
        return f'Report — {self.task.task_number}'

    @property
    def is_overrun(self):
        """True when the hours worked ran well past the task's estimate —
        either the job was harder than expected or the estimate was off;
        both are worth a supervisor's look. False with no estimate set.
        """
        estimated = self.task.estimated_hours
        return bool(estimated) and self.labour_hours > estimated * OVERRUN_RATIO

    @property
    def tapped_hours(self):
        """Time on the job by the app's own taps — the first "start" to
        the report first being filed, minus time paused — which nobody can
        backdate. A manager's correction of a tap's time counts (effective_at).
        None without both taps. Uses task.events.all(), so a list of reports
        should prefetch task__events.
        """
        events = sorted(self.task.events.all(), key=lambda event: event.effective_at)
        started = next((e.effective_at for e in events if e.event_type == TaskEvent.EventType.STARTED), None)
        filed = next(
            (e.effective_at for e in events if e.event_type == TaskEvent.EventType.REPORT_SUBMITTED), None,
        )
        if started is None or filed is None or filed <= started:
            return None
        worked = filed - started
        paused_at = None
        for event in events:
            if not started <= event.effective_at <= filed:
                continue
            if event.event_type == TaskEvent.EventType.PAUSED:
                paused_at = event.effective_at
            elif event.event_type == TaskEvent.EventType.RESUMED and paused_at:
                worked -= event.effective_at - paused_at
                paused_at = None
        return (Decimal(worked.total_seconds()) / 3600).quantize(Decimal('0.01'))

    @property
    def has_taps_away_from_site(self):
        """True when an on-site tap on this job (arrived, started, report…)
        came from away from the site. Uses task.events.all() like
        tapped_hours."""
        return any(
            event.location_status == TaskEvent.LocationStatus.AWAY for event in self.task.events.all()
        )

    @property
    def is_above_tapped(self):
        """True when the typed hours are well above the tapped time — worth
        a supervisor asking about. Typing fewer hours is never flagged.
        """
        tapped = self.tapped_hours
        return tapped is not None and self.labour_hours > max(
            tapped * ABOVE_TAPPED_RATIO, tapped + ABOVE_TAPPED_SLACK,
        )


class PartUsed(models.Model):
    report = models.ForeignKey(
        WorkReport, on_delete=models.CASCADE, related_name='parts_used',
        verbose_name=_('report'),
    )
    part_code = models.CharField(_('part code'), max_length=50)
    description = models.CharField(_('description'), max_length=200, blank=True)
    quantity = models.PositiveIntegerField(_('quantity'))
    unit_cost = models.DecimalField(_('unit cost'), max_digits=10, decimal_places=2)
    currency_code = models.CharField(
        _('currency code'), max_length=3,
        help_text=_('never store an amount without its currency'),
    )

    class Meta:
        verbose_name = _('part used')
        verbose_name_plural = _('parts used')
        ordering = ['report', 'part_code']

    def __str__(self):
        return f'{self.report} — {self.part_code}'


class CustomerFeedback(models.Model):
    """A rating request sent to the customer once their task is closed.
    Reached through `token`, not a login — the customer is never a user of
    this system, so the link is the only thing standing in for one.
    """

    RATING_CHOICES = [(i, str(i)) for i in range(1, 6)]

    task = models.OneToOneField(
        Task, on_delete=models.CASCADE, related_name='feedback',
        verbose_name=_('task'),
    )
    token = models.CharField(_('token'), max_length=43, unique=True, editable=False)
    requested_at = models.DateTimeField(_('requested at'))
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='feedback_requests_sent',
        verbose_name=_('requested by'),
    )
    rating = models.PositiveSmallIntegerField(_('rating'), choices=RATING_CHOICES, null=True, blank=True)
    comment = models.TextField(_('comment'), blank=True)
    submitted_at = models.DateTimeField(_('submitted at'), null=True, blank=True)

    class Meta:
        verbose_name = _('customer feedback')
        verbose_name_plural = _('customer feedback')
        ordering = ['-requested_at']

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'Feedback — {self.task.task_number}'
