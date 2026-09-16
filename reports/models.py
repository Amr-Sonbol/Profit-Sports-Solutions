import secrets

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from tasks.models import Task


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
    submitted_at = models.DateTimeField(_('submitted at'))
    approved_at = models.DateTimeField(_('approved at'), null=True, blank=True)
    rejection_reason = models.TextField(_('rejection reason'), blank=True)

    class Meta:
        verbose_name = _('work report')
        verbose_name_plural = _('work reports')
        ordering = ['-submitted_at']

    def __str__(self):
        return f'Report — {self.task.task_number}'


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
    """A rating request sent to the customer once their report is approved.
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
