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
