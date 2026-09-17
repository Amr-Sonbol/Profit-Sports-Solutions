from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from customers.models import Asset, Site
from people.models import Technician
from reference.models import Brand, Country, Skill, TaskType


class Task(models.Model):
    class Priority(models.TextChoices):
        LOW = 'low', _('Low')
        NORMAL = 'normal', _('Normal')
        HIGH = 'high', _('High')
        EMERGENCY = 'emergency', _('Emergency')

    class Source(models.TextChoices):
        PHONE = 'phone', _('Phone')
        WHATSAPP = 'whatsapp', _('WhatsApp')
        EMAIL = 'email', _('Email')
        INTERNAL = 'internal', _('Internal')
        PORTAL = 'portal', _('Customer ticket')

    class BillingType(models.TextChoices):
        WARRANTY = 'warranty', _('Warranty')
        CONTRACT = 'contract', _('Contract')
        CHARGEABLE = 'chargeable', _('Chargeable')
        GOODWILL = 'goodwill', _('Goodwill')

    class Status(models.TextChoices):
        NEW = 'new', _('New')
        ASSIGNED = 'assigned', _('Assigned')
        ACCEPTED = 'accepted', _('Accepted')
        IN_PROGRESS = 'in_progress', _('In progress')
        COMPLETED = 'completed', _('Completed')
        CLOSED = 'closed', _('Closed')
        BLOCKED = 'blocked', _('Blocked')
        CANCELLED = 'cancelled', _('Cancelled')

    task_number = models.CharField(
        _('task number'), max_length=30, unique=True,
        help_text=_('auto-generated per country, e.g. AE-0001'),
    )
    site = models.ForeignKey(
        Site, on_delete=models.PROTECT, related_name='tasks',
        verbose_name=_('site'), help_text=_('the only certain field at creation'),
    )
    task_type = models.ForeignKey(
        TaskType, on_delete=models.PROTECT, related_name='tasks',
        null=True, blank=True, verbose_name=_('task type'),
    )
    brand = models.ForeignKey(
        Brand, on_delete=models.PROTECT, related_name='tasks',
        null=True, blank=True, verbose_name=_('brand'),
    )
    required_skill = models.ForeignKey(
        Skill, on_delete=models.PROTECT, related_name='tasks',
        null=True, blank=True, verbose_name=_('required skill'),
    )
    min_level = models.PositiveSmallIntegerField(_('minimum level'), null=True, blank=True)
    description = models.TextField(_('description'), blank=True, help_text=_('what the customer reported'))
    priority = models.CharField(_('priority'), max_length=20, choices=Priority.choices)
    source = models.CharField(_('source'), max_length=20, choices=Source.choices)
    is_warranty = models.BooleanField(_('is warranty'), null=True, blank=True)
    billing_type = models.CharField(_('billing type'), max_length=20, choices=BillingType.choices)
    reported_at = models.DateTimeField(_('reported at'))
    promised_at = models.DateTimeField(
        _('promised at'), null=True, blank=True, help_text=_('what the customer is owed'),
    )
    scheduled_for = models.DateTimeField(
        _('scheduled for'), null=True, blank=True,
        help_text=_('the day the supervisor planned'),
    )
    estimated_hours = models.DecimalField(
        _('estimated hours'), max_digits=4, decimal_places=2, null=True, blank=True,
        help_text=_('roughly how long the job should take — shown as an estimated finish time'),
    )
    status = models.CharField(_('status'), max_length=20, choices=Status.choices, default=Status.NEW)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='tasks_created',
        verbose_name=_('created by'),
    )
    schedule_notified_at = models.DateTimeField(
        _('schedule notified at'), null=True, blank=True,
        help_text=_('when the customer was last emailed about the scheduled visit — never automatic'),
    )
    schedule_notified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name='tasks_schedule_notified', verbose_name=_('schedule notified by'),
    )

    class Meta:
        verbose_name = _('task')
        verbose_name_plural = _('tasks')
        ordering = ['-reported_at']
        indexes = [
            models.Index(fields=['status', 'promised_at']),
            models.Index(fields=['site', 'status']),
            models.Index(fields=['scheduled_for', 'status']),
        ]

    @property
    def estimated_finish(self):
        """scheduled_for + estimated_hours, if both are known — None otherwise."""
        if self.scheduled_for is None or self.estimated_hours is None:
            return None
        return self.scheduled_for + timedelta(hours=float(self.estimated_hours))

    def __str__(self):
        return self.task_number


class CustomerTicket(models.Model):
    """A complaint or request submitted directly by a customer, no login
    required. Self-identified, not yet linked to a real site — a
    supervisor reviews it and either converts it into a task (picking an
    existing site or creating a new one, same as task creation always
    allows) or dismisses it.
    """

    class Status(models.TextChoices):
        NEW = 'new', _('New')
        CONVERTED = 'converted', _('Converted to task')
        DISMISSED = 'dismissed', _('Dismissed')

    country = models.ForeignKey(
        Country, on_delete=models.PROTECT, related_name='customer_tickets',
        verbose_name=_('country'),
    )
    company_name = models.CharField(_('company / customer name'), max_length=150)
    site_description = models.CharField(
        _('site / location'), max_length=150,
        help_text=_('branch name or address, as the customer describes it'),
    )
    contact_name = models.CharField(_('contact name'), max_length=150)
    contact_phone = models.CharField(_('contact phone'), max_length=30)
    contact_email = models.EmailField(_('contact email'), blank=True)
    description = models.TextField(_('description'), help_text=_('what the customer reported'))
    submitted_at = models.DateTimeField(_('submitted at'))
    status = models.CharField(_('status'), max_length=20, choices=Status.choices, default=Status.NEW)
    assigned_to = models.ForeignKey(
        Technician, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tickets_assigned', verbose_name=_('assigned to'),
        help_text=_('who is handling this — a technician or a supervisor, not necessarily who converts it'),
    )
    assigned_at = models.DateTimeField(_('assigned at'), null=True, blank=True)
    task = models.OneToOneField(
        Task, on_delete=models.SET_NULL, null=True, blank=True, related_name='ticket',
        verbose_name=_('task'),
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name='tickets_reviewed', verbose_name=_('reviewed by'),
    )
    reviewed_at = models.DateTimeField(_('reviewed at'), null=True, blank=True)
    dismissal_reason = models.CharField(_('dismissal reason'), max_length=255, blank=True)

    class Meta:
        verbose_name = _('customer ticket')
        verbose_name_plural = _('customer tickets')
        ordering = ['-submitted_at']

    def __str__(self):
        return f'{self.company_name} — {self.site_description}'


class TaskAssignment(models.Model):
    class Role(models.TextChoices):
        LEAD = 'lead', _('Lead')
        HELPER = 'helper', _('Helper')

    class EndReason(models.TextChoices):
        SICK = 'sick', _('Sick')
        LEAVE = 'leave', _('Leave')
        OVERLOADED = 'overloaded', _('Overloaded')
        SKILL_MISMATCH = 'skill_mismatch', _('Skill mismatch')
        CUSTOMER_REQUEST = 'customer_request', _('Customer request')
        EMERGENCY = 'emergency', _('Emergency')
        VEHICLE = 'vehicle', _('Vehicle')
        OTHER = 'other', _('Other')

    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name='assignments',
        verbose_name=_('task'),
    )
    technician = models.ForeignKey(
        Technician, on_delete=models.PROTECT, related_name='assignments',
        verbose_name=_('technician'),
    )
    role = models.CharField(_('role'), max_length=20, choices=Role.choices)
    assigned_at = models.DateTimeField(_('assigned at'))
    is_active = models.BooleanField(_('active'), default=True, help_text=_('false once replaced'))
    ended_at = models.DateTimeField(_('ended at'), null=True, blank=True)
    end_reason = models.CharField(
        _('end reason'), max_length=20, choices=EndReason.choices, blank=True,
        help_text=_('required on reassignment — one tap, never free text'),
    )

    class Meta:
        verbose_name = _('task assignment')
        verbose_name_plural = _('task assignments')
        ordering = ['task', '-assigned_at']
        indexes = [
            models.Index(fields=['technician', 'is_active']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['task'],
                condition=models.Q(role='lead', is_active=True),
                name='one_active_lead_per_task',
            ),
        ]

    def __str__(self):
        return f'{self.task.task_number} — {self.technician.full_name} ({self.role})'


class TaskEvent(models.Model):
    class EventType(models.TextChoices):
        CREATED = 'created', _('Created')
        ASSIGNED = 'assigned', _('Assigned')
        REASSIGNED = 'reassigned', _('Reassigned')
        RESCHEDULED = 'rescheduled', _('Rescheduled')
        DELAY_NOTICE = 'delay_notice', _('Delay notice sent')
        ACCEPTED = 'accepted', _('Accepted')
        EN_ROUTE = 'en_route', _('En route')
        ARRIVED = 'arrived', _('Arrived')
        BLOCKED = 'blocked', _('Blocked')
        STARTED = 'started', _('Started')
        COMPLETED = 'completed', _('Completed')
        REPORT_SUBMITTED = 'report_submitted', _('Report submitted')
        REPORT_REJECTED = 'report_rejected', _('Report rejected')
        REPORT_APPROVED = 'report_approved', _('Report approved')
        CLOSED = 'closed', _('Closed')
        REOPENED = 'reopened', _('Reopened')
        CANCELLED = 'cancelled', _('Cancelled')

    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name='events',
        verbose_name=_('task'),
    )
    event_type = models.CharField(_('event type'), max_length=30, choices=EventType.choices)
    occurred_at = models.DateTimeField(_('occurred at'))
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='task_events',
        verbose_name=_('actor'),
    )
    corrected_at = models.DateTimeField(
        _('corrected at'), null=True, blank=True,
        help_text=_("supervisor's correction"),
    )
    corrected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='task_event_corrections',
        null=True, blank=True, verbose_name=_('corrected by'),
        help_text=_('supervisors only'),
    )
    note = models.TextField(_('note'), blank=True)

    class Meta:
        verbose_name = _('task event')
        verbose_name_plural = _('task events')
        ordering = ['task', 'occurred_at']
        indexes = [
            models.Index(fields=['task', 'occurred_at']),
            models.Index(fields=['event_type', 'occurred_at']),
        ]

    def __str__(self):
        return f'{self.task.task_number} — {self.event_type}'


class TaskAttachment(models.Model):
    class StorageKind(models.TextChoices):
        FILE = 'file', _('File')
        LINK = 'link', _('Link')

    class MediaType(models.TextChoices):
        PHOTO = 'photo', _('Photo')
        VIDEO = 'video', _('Video')
        DOCUMENT = 'document', _('Document')

    class Purpose(models.TextChoices):
        FAULT = 'fault', _('Fault')
        SERIAL_PLATE = 'serial_plate', _('Serial plate')
        BEFORE = 'before', _('Before')
        AFTER = 'after', _('After')

    class Source(models.TextChoices):
        CUSTOMER = 'customer', _('Customer')
        SUPERVISOR = 'supervisor', _('Supervisor')
        TECHNICIAN = 'technician', _('Technician')

    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name='attachments',
        verbose_name=_('task'),
    )
    storage_kind = models.CharField(_('storage kind'), max_length=10, choices=StorageKind.choices)
    url = models.URLField(_('URL'))
    media_type = models.CharField(_('media type'), max_length=20, choices=MediaType.choices)
    purpose = models.CharField(_('purpose'), max_length=20, choices=Purpose.choices)
    source = models.CharField(_('source'), max_length=20, choices=Source.choices)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='task_attachments',
        verbose_name=_('uploaded by'),
    )
    uploaded_at = models.DateTimeField(_('uploaded at'))

    class Meta:
        verbose_name = _('task attachment')
        verbose_name_plural = _('task attachments')
        ordering = ['task', 'uploaded_at']

    def __str__(self):
        return f'{self.task.task_number} — {self.purpose}'


class TaskAsset(models.Model):
    """Which machines the task covers. Populated by the technician during the work."""

    class Outcome(models.TextChoices):
        REPAIRED = 'repaired', _('Repaired')
        REPLACED = 'replaced', _('Replaced')
        NOT_REPAIRABLE = 'not_repairable', _('Not repairable')
        INSPECTED_OK = 'inspected_ok', _('Inspected OK')

    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name='task_assets',
        verbose_name=_('task'),
    )
    asset = models.ForeignKey(
        Asset, on_delete=models.PROTECT, related_name='task_assets',
        verbose_name=_('asset'),
    )
    outcome = models.CharField(_('outcome'), max_length=20, choices=Outcome.choices)

    class Meta:
        verbose_name = _('task asset')
        verbose_name_plural = _('task assets')
        ordering = ['task', 'asset']
        indexes = [
            models.Index(fields=['asset']),
        ]

    def __str__(self):
        return f'{self.task.task_number} — {self.asset}'
