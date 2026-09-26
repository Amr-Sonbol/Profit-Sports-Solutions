import secrets
from datetime import timedelta

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from customers.models import Asset, Customer, Site
from people.models import Technician
from reference.models import Brand, Country, Skill, TaskType

# A customer's own phone photos/videos of the fault — kept separate from
# TaskAttachment's own list (tasks/forms.py) since this one has no LINK
# option, and no uploaded_by of its own — the ticket itself already
# records who submitted it (ticket.customer).
ALLOWED_TICKET_ATTACHMENT_EXTENSIONS = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'heic', 'mp4', 'mov', 'webm', 'pdf']
MAX_TICKET_ATTACHMENT_BYTES = 25 * 1024 * 1024
# A cap on file count, not just size — without this a single submission
# could attach an unbounded number of files and exhaust storage.
MAX_TICKET_ATTACHMENT_COUNT = 10

# The paperwork trail for a task — quotation, factory offer, invoice,
# delivery note. Almost always a PDF export; jpg/png covers a photo of
# a paper one.
ALLOWED_TASK_DOCUMENT_EXTENSIONS = ['pdf', 'jpg', 'jpeg', 'png']
MAX_TASK_DOCUMENT_BYTES = 10 * 1024 * 1024


class ShippingCompany(models.TextChoices):
    """Who's carrying the parts — shared by Task and CustomerTicket, same
    as pak_reference_number/shipping_tracking_number, which this sits
    alongside on both. A fixed list, not free text, for the same reason
    task_type is one: 'FedEx' vs 'Fedex' vs 'fedex' drifts within a year.
    """
    DHL = 'dhl', _('DHL')
    FEDEX = 'fedex', _('FedEx')
    UPS = 'ups', _('UPS')
    ARAMEX = 'aramex', _('Aramex')
    TNT = 'tnt', _('TNT')
    LOCAL_COURIER = 'local_courier', _('Local courier')
    OTHER = 'other', _('Other')


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
    scheduled_date = models.DateField(
        _('scheduled date'), null=True, blank=True,
        help_text=_(
            'set by a manager scheduling day-only — the day is fixed while '
            'scheduled_for stays empty until a supervisor fills in the time'
        ),
    )
    schedule_time_locked = models.BooleanField(
        _('schedule time locked'), default=False,
        help_text=_(
            'set automatically — true whenever a manager is the one who last set '
            'scheduled_for; a supervisor doing so leaves it false. Locked means a '
            'supervisor can no longer change scheduled_for directly and must request a change'
        ),
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
    responsible_supervisor = models.ForeignKey(
        Technician, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tasks_responsible_for', verbose_name=_('responsible supervisor'),
        help_text=_(
            'who is accountable for staffing this task — not necessarily who created it, '
            'and not the same as the technician actually assigned to do the work'
        ),
    )
    pak_reference_number = models.CharField(_('PAK reference number'), max_length=100, blank=True)
    shipping_company = models.CharField(
        _('shipping company'), max_length=20, choices=ShippingCompany.choices, blank=True,
    )
    shipping_tracking_number = models.CharField(_('shipping tracking number'), max_length=100, blank=True)
    quotation = models.FileField(
        _('quotation'), upload_to='task_documents/', null=True, blank=True,
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_TASK_DOCUMENT_EXTENSIONS)],
    )
    quotation_uploaded_at = models.DateTimeField(_('quotation uploaded at'), null=True, blank=True)
    factory_offer = models.FileField(
        _('factory offer'), upload_to='task_documents/', null=True, blank=True,
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_TASK_DOCUMENT_EXTENSIONS)],
    )
    factory_offer_uploaded_at = models.DateTimeField(_('factory offer uploaded at'), null=True, blank=True)
    invoice = models.FileField(
        _('invoice'), upload_to='task_documents/', null=True, blank=True,
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_TASK_DOCUMENT_EXTENSIONS)],
    )
    invoice_uploaded_at = models.DateTimeField(_('invoice uploaded at'), null=True, blank=True)
    delivery_note = models.FileField(
        _('delivery note'), upload_to='task_documents/', null=True, blank=True,
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_TASK_DOCUMENT_EXTENSIONS)],
        help_text=_('the shipment paperwork — uploaded once the parts arrive'),
    )
    delivery_note_uploaded_at = models.DateTimeField(_('delivery note uploaded at'), null=True, blank=True)
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
    """A complaint or request submitted by a logged-in customer, picking
    one of their own registered sites — a supervisor reviews it and
    either converts it into a task or dismisses it. Checking on it
    afterwards (and replying) doesn't require staying logged in — see
    the token field below.
    """

    class Status(models.TextChoices):
        NEW = 'new', _('New')
        CONVERTED = 'converted', _('Converted to task')
        DISMISSED = 'dismissed', _('Dismissed')
        CLOSED = 'closed', _('Closed')

    ticket_number = models.CharField(
        _('ticket number'), max_length=30, unique=True,
        help_text=_('auto-generated per country, e.g. AE-T0001 — the same prefix a task number uses, marked with a T so the two are never confused'),
    )
    country = models.ForeignKey(
        Country, on_delete=models.PROTECT, related_name='customer_tickets',
        verbose_name=_('country'),
    )
    customer = models.ForeignKey(
        Customer, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tickets', verbose_name=_('customer'),
        help_text=_(
            'set automatically — at submission if the customer was logged in, '
            'otherwise once the ticket is converted to a task'
        ),
    )
    site = models.ForeignKey(
        Site, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tickets', verbose_name=_('site'),
        help_text=_(
            'set when a logged-in customer picked one of their own registered sites — '
            'carries straight through to the resulting task with no re-matching, '
            'and only an admin can change it once set'
        ),
    )
    company_name = models.CharField(_('gym name'), max_length=150)
    customer_code = models.CharField(
        _('customer code'), max_length=50, blank=True,
        help_text=_('set automatically for a logged-in customer, from their own account'),
    )
    site_description = models.CharField(
        _('site / location'), max_length=150,
        help_text=_('branch name, as the customer describes it'),
    )
    # default='' only backfills existing rows cleanly — ModelForm validation
    # still enforces this as required on new submissions (blank=False).
    site_address = models.TextField(_('gym address'), default='')
    contact_name = models.CharField(_('full contact name'), max_length=150)
    contact_phone = models.CharField(_('contact phone number'), max_length=30)
    contact_email = models.EmailField(_('contact email address'), blank=True)
    shipping_address = models.TextField(
        _('shipping address'), blank=True,
        help_text=_('where replacement parts should be delivered, if different from the site itself'),
    )
    serial_numbers = models.TextField(
        _('serial number(s)'), default='',
        help_text=_('please list each affected machine on a new line'),
    )
    description = models.TextField(
        _('description'),
        help_text=_('please describe the issue for each machine separately (mention the serial number for each one)'),
    )
    notes = models.TextField(
        _('anything else we should know'), blank=True,
    )
    submitted_at = models.DateTimeField(_('submitted at'))
    status = models.CharField(_('status'), max_length=20, choices=Status.choices, default=Status.NEW)
    assigned_to = models.ForeignKey(
        Technician, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tickets_assigned', verbose_name=_('assigned to'),
        help_text=_('who is handling this — a supervisor or manager, not necessarily who converts it'),
    )
    assigned_at = models.DateTimeField(_('assigned at'), null=True, blank=True)
    pak_reference_number = models.CharField(_('PAK reference number'), max_length=100, blank=True)
    shipping_company = models.CharField(
        _('shipping company'), max_length=20, choices=ShippingCompany.choices, blank=True,
    )
    shipping_tracking_number = models.CharField(_('shipping tracking number'), max_length=100, blank=True)
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
    close_reason = models.CharField(
        _('close reason'), max_length=255, blank=True,
        help_text=_('resolved without needing a task — advice given, handled by phone, etc.'),
    )
    token = models.CharField(
        _('token'), max_length=43, unique=True, editable=False,
        help_text=_('lets the customer check this ticket’s status without an account — see reports.CustomerFeedback'),
    )

    class Meta:
        verbose_name = _('customer ticket')
        verbose_name_plural = _('customer tickets')
        ordering = ['-submitted_at']

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.ticket_number} — {self.company_name}'


class TicketReply(models.Model):
    """One message in the back-and-forth on a ticket — either side, in
    order. Open while the ticket is still new, or converted but the
    resulting task isn't finished yet — read-only once dismissed,
    closed directly, or once that task itself is closed (a cancelled
    task does not close it — see _ticket_is_open, tasks/views.py).
    `sent_by` is a real login either way: staff always
    has one, and a customer does too when they replied through their
    portal login rather than the anonymous token page.
    """

    class Sender(models.TextChoices):
        STAFF = 'staff', _('Staff')
        CUSTOMER = 'customer', _('Customer')

    ticket = models.ForeignKey(
        CustomerTicket, on_delete=models.CASCADE, related_name='replies',
        verbose_name=_('ticket'),
    )
    sender = models.CharField(_('sender'), max_length=10, choices=Sender.choices)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='ticket_replies_sent', verbose_name=_('sent by'),
    )
    message = models.TextField(_('message'))
    attachment = models.FileField(
        _('attachment'), upload_to='ticket_reply_attachments/', null=True, blank=True,
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_TICKET_ATTACHMENT_EXTENSIONS)],
    )
    is_quotation = models.BooleanField(
        _('is quotation'), default=False,
        help_text=_('sends the dedicated quotation email (with the attachment) instead of a plain reply notice — staff only'),
    )
    sent_at = models.DateTimeField(_('sent at'))

    class Meta:
        verbose_name = _('ticket reply')
        verbose_name_plural = _('ticket replies')
        ordering = ['sent_at']

    def __str__(self):
        return f'{self.ticket} — {self.get_sender_display()} @ {self.sent_at}'


class TicketInternalNote(models.Model):
    """A manager-tier-only progress note on a ticket — tracking what state
    things are in behind the scenes. Never shown to the customer, and
    unlike TicketReply, never shown to a supervisor or technician either —
    only Manager and Admin.
    """

    ticket = models.ForeignKey(
        CustomerTicket, on_delete=models.CASCADE, related_name='internal_notes',
        verbose_name=_('ticket'),
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='ticket_internal_notes', verbose_name=_('author'),
    )
    message = models.TextField(_('note'))
    created_at = models.DateTimeField(_('created at'))

    class Meta:
        verbose_name = _('ticket internal note')
        verbose_name_plural = _('ticket internal notes')
        ordering = ['created_at']

    def __str__(self):
        return f'{self.ticket} — {self.created_at}'


class CustomerTicketAttachment(models.Model):
    """A customer's own phone photo or video of the fault, uploaded with
    the ticket — no login, so no uploaded_by; a plain FileField, since
    there's no external-link case to support the way TaskAttachment has.
    """

    ticket = models.ForeignKey(
        CustomerTicket, on_delete=models.CASCADE, related_name='attachments',
        verbose_name=_('ticket'),
    )
    file = models.FileField(
        _('file'), upload_to='ticket_attachments/',
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_TICKET_ATTACHMENT_EXTENSIONS)],
    )
    uploaded_at = models.DateTimeField(_('uploaded at'))

    class Meta:
        verbose_name = _('ticket attachment')
        verbose_name_plural = _('ticket attachments')
        ordering = ['ticket', 'uploaded_at']

    def __str__(self):
        return f'{self.ticket} — {self.file.name}'


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
        SCHEDULE_CHANGE_REQUESTED = 'schedule_change_requested', _('Schedule change requested')
        SCHEDULE_CHANGE_APPROVED = 'schedule_change_approved', _('Schedule change approved')
        SCHEDULE_CHANGE_DENIED = 'schedule_change_denied', _('Schedule change denied')
        ACCEPTED = 'accepted', _('Accepted')
        EN_ROUTE = 'en_route', _('En route')
        ARRIVED = 'arrived', _('Arrived')
        BLOCKED = 'blocked', _('Blocked')
        STARTED = 'started', _('Started')
        PAUSED = 'paused', _('Paused for the day')
        RESUMED = 'resumed', _('Resumed')
        COMPLETED = 'completed', _('Completed')
        REPORT_SUBMITTED = 'report_submitted', _('Report submitted')
        REPORT_REJECTED = 'report_rejected', _('Report rejected')
        REPORT_APPROVED = 'report_approved', _('Report approved')
        CLOSED = 'closed', _('Closed')
        REOPENED = 'reopened', _('Reopened')
        CANCELLED = 'cancelled', _('Cancelled')
        NEGLIGENCE = 'negligence', _('Negligence reported')

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


class ScheduleChangeRequest(models.Model):
    """A supervisor asking to move a task's schedule once a manager has
    locked it (Task.schedule_time_locked) — the only case a supervisor
    can't just edit scheduled_for themselves. A manager reviews it
    directly from the task's own detail page, same as approving a
    report or closing a task — no separate queue screen.
    """
    class Status(models.TextChoices):
        PENDING = 'pending', _('Pending')
        APPROVED = 'approved', _('Approved')
        DENIED = 'denied', _('Denied')

    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name='schedule_change_requests',
        verbose_name=_('task'),
    )
    requested_by = models.ForeignKey(
        Technician, on_delete=models.PROTECT, related_name='schedule_change_requests',
        verbose_name=_('requested by'),
    )
    requested_scheduled_for = models.DateTimeField(_('requested scheduled for'))
    reason = models.CharField(_('reason'), max_length=255, blank=True)
    status = models.CharField(_('status'), max_length=10, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name='schedule_change_requests_reviewed', verbose_name=_('reviewed by'),
    )
    reviewed_at = models.DateTimeField(_('reviewed at'), null=True, blank=True)
    review_note = models.CharField(_('review note'), max_length=255, blank=True)
    created_at = models.DateTimeField(_('created at'))

    class Meta:
        verbose_name = _('schedule change request')
        verbose_name_plural = _('schedule change requests')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.task.task_number} — {self.get_status_display()}'


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
        DELIVERY_NOTE = 'delivery_note', _('Delivery note')
        WRITTEN_REPORT = 'written_report', _('Written report')
        OTHER = 'other', _('Other')

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


class TaskProduct(models.Model):
    """One machine's line on an installation or loading task — the same
    items as on the delivery note (that part still just needs a product
    code; the rest is blank for a simple parts line), plus the per-unit
    checklist a supervisor fills in for an actual machine being installed:
    custom color per component, and what it got swapped for if the
    ordered one wasn't available. The delivery note PDF (Task.delivery_note)
    already holds the same product-code/quantity information as a scanned/
    exported document; this is the same data kept structured, so it can be
    searched and listed rather than only read off the file. Entered from
    the task's Edit screen, replaced wholesale on every save — not an
    append-only log the way task_event is.
    """
    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name='products',
        verbose_name=_('task'),
    )
    product_code = models.CharField(_('product code'), max_length=100)
    serial_number = models.CharField(_('serial number'), max_length=100, blank=True)
    quantity = models.PositiveIntegerField(_('quantity'), default=1)
    replacement = models.CharField(
        _('replacement'), max_length=150, blank=True,
        help_text=_('which machine this was swapped in for, if the one ordered wasn’t available'),
    )
    frame = models.CharField(_('frame color'), max_length=100, blank=True)
    arm = models.CharField(_('arm color'), max_length=100, blank=True)
    padding = models.CharField(_('padding color'), max_length=100, blank=True)
    trim = models.CharField(_('trim color'), max_length=100, blank=True)
    comment = models.TextField(_('comment'), blank=True)
    note = models.TextField(_('note'), blank=True)

    class Meta:
        verbose_name = _('task product')
        verbose_name_plural = _('task products')
        ordering = ['task', 'product_code']

    def __str__(self):
        return f'{self.task.task_number} — {self.product_code}'
