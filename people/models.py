import secrets

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils.translation import gettext_lazy as _

from reference.models import ConductArea, Country, Skill
from spots.uploads import SafeFileValidator

# Shared by TechnicianSkill, TechnicianConduct, and their assessment logs.
SKILL_LEVEL_CHOICES = [(i, str(i)) for i in range(1, 5)]

# "Has led this work alone, repeatedly, with no callbacks" — the bar a
# skill or conduct area must clear to count toward the technician
# certification (docs/database_design_v2.md, §3).
RELIABLE_LEVEL = 3

# A plain FileField, not ImageField, so a headshot upload doesn't need
# Pillow — same reasoning as tasks.forms.TaskAttachmentUploadForm.
ALLOWED_PHOTO_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp']
MAX_PHOTO_UPLOAD_BYTES = 5 * 1024 * 1024

# Proof of a skill actually being performed — photo or short video, same
# allowlist and ceiling as every other photo/video upload in the app
# (tasks.forms.ALLOWED_MEDIA_EXTENSIONS).
ALLOWED_SKILL_EVIDENCE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'heic', 'mp4', 'mov', 'webm']
MAX_SKILL_EVIDENCE_UPLOAD_BYTES = 25 * 1024 * 1024


# `MEDIA_URL` is served with no per-file login check (see
# tasks.models for the same reasoning), so a flat `<folder>/<original
# filename>` path is as good as public — anyone who gets or guesses the
# URL can view it with no login at all. Every upload gets its own random
# directory instead; the original filename stays underneath it purely
# so a human looking at the path can still tell what it is.
def _technician_photo_path(instance, filename):
    return f'technician_photos/{secrets.token_hex(16)}/{filename}'


def _skill_evidence_path(instance, filename):
    return f'skill_evidence/{secrets.token_hex(16)}/{filename}'


class Technician(models.Model):
    class Role(models.TextChoices):
        TECHNICIAN = 'technician', _('Technician')
        SUPERVISOR = 'supervisor', _('Supervisor')
        MANAGER = 'manager', _('Manager')
        SUPPORT_MANAGER = 'support_manager', _('Technical Support Manager')
        COUNTRY_SUPPORT = 'country_support', _('Country Support Manager')
        WAREHOUSE_MANAGER = 'warehouse_manager', _('Warehouse Manager')
        OPERATIONS_MANAGER = 'operations_manager', _('Operations Manager')
        # Like a Manager, in every country; no customer requests (docs/access_rules.md).
        PROJECT_MANAGER = 'project_manager', _('Project Manager')
        ADMIN = 'admin', _('Admin')

    class EmploymentType(models.TextChoices):
        STAFF = 'staff', _('Staff')
        FREELANCE = 'freelance', _('Freelance')

    class Language(models.TextChoices):
        AR = 'ar', _('Arabic')
        EN = 'en', _('English')

    class UnavailableReason(models.TextChoices):
        SICK = 'sick', _('Sick')
        LEAVE = 'leave', _('Leave')
        HOLIDAY = 'holiday', _('Holiday')
        OTHER = 'other', _('Other')

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='technician',
        verbose_name=_('user'),
        help_text=_("created by a manager from the technician's Edit screen, in the same way as a customer's "
                    'portal login'),
    )
    must_change_password = models.BooleanField(
        _('must change password'), default=False,
        help_text=_(
            'set when a manager creates the login with a temporary system-generated password; the user must '
            'set their own password at first sign-in before accessing anything else',
        ),
    )
    setup_link_sent_at = models.DateTimeField(
        _('setup link sent at'), null=True, blank=True,
        help_text=_('when the last setup link was emailed; it works once and expires after 7 days'),
    )
    country = models.ForeignKey(
        Country, on_delete=models.PROTECT, related_name='technicians',
        verbose_name=_('country'),
    )
    full_name = models.CharField(_('full name'), max_length=150)
    # What they're called, apart from what they can do (`role`): a General
    # Manager with an Operations Manager's access, a "Customer Service
    # Coordinator" with Country Support's. Shown wherever a person is listed
    # (`title`); the access role itself only where access is managed.
    job_title = models.CharField(
        _('job title'), max_length=100, blank=True,
        help_text=_("What they're called, for example General Manager. Leave empty to use the role's name."),
    )
    phone = models.CharField(_('phone'), max_length=30, blank=True)
    language = models.CharField(
        _('language'), max_length=2, choices=Language.choices,
        help_text=_('per person, not per country'),
    )
    # The built-in role whose fixed rules apply (what the code checks: area,
    # stepping in, approvals...). With a custom role, its base: the person's own
    # switches then come from the custom role (permission_role).
    role = models.CharField(_('role'), max_length=20, choices=Role.choices)
    custom_role = models.ForeignKey(
        'people.CustomRole', on_delete=models.PROTECT, null=True, blank=True, related_name='people',
        verbose_name=_('custom role'),
        help_text=_('a role the Admin made, based on `role`; its own switches replace the base role\'s'),
    )
    employment_type = models.CharField(
        _('employment type'), max_length=20, choices=EmploymentType.choices,
    )
    has_transport = models.BooleanField(_('has transport'), default=False)
    can_carry_large = models.BooleanField(
        _('can carry large equipment'), default=False,
        help_text=_('can move a treadmill motor or locker bank'),
    )
    hired_on = models.DateField(_('hired on'), null=True, blank=True)
    is_active = models.BooleanField(_('active'), default=True)
    deactivation_reason = models.CharField(
        _('deactivation reason'), max_length=255, blank=True,
        help_text=_('reason for deactivation, e.g. resignation or termination'),
    )
    is_available = models.BooleanField(
        _('available'), default=True,
        help_text=_('whether this technician can currently be assigned work; separate from active status'),
    )
    unavailable_reason = models.CharField(
        _('unavailable reason'), max_length=20, choices=UnavailableReason.choices, blank=True,
    )
    photo = models.FileField(
        _('photo'), upload_to=_technician_photo_path, null=True, blank=True,
        validators=[SafeFileValidator(allowed_extensions=ALLOWED_PHOTO_EXTENSIONS)],
        help_text=_('shown on the roster, boards and task details'),
    )

    class Meta:
        verbose_name = _('technician')
        verbose_name_plural = _('technicians')
        ordering = ['full_name']

    def __str__(self):
        return self.full_name

    def save(self, *args, **kwargs):
        """An admin's linked login always gets full Django-admin access —
        the in-app "do everything" role should also mean everything at
        /admin/ (every model, every action, plus Django's own built-in
        add/change/delete audit trail there). One-way: giving admin the
        role grants it; it's never silently revoked by a later save, so
        changing someone's role back down doesn't unexpectedly lock out
        a login that was also made a Django superuser some other way.
        """
        super().save(*args, **kwargs)
        if self.role == self.Role.ADMIN and self.user_id and not (self.user.is_staff and self.user.is_superuser):
            self.user.is_staff = True
            self.user.is_superuser = True
            self.user.save(update_fields=['is_staff', 'is_superuser'])

    @property
    def is_manager_tier(self):
        """Manager, the Operations Manager, the Technical Support Manager and
        admin share every manager-only screen and action — cross-country
        reach, approvals, the fixed-floor reference-data screens (skills,
        brands, countries, ...), the monthly report. Admin is the one thing
        on top: only admin can reach Roles & permissions itself (see
        require_admin) — checked separately with role == Role.ADMIN.
        """
        return self.role in (
            self.Role.MANAGER, self.Role.OPERATIONS_MANAGER, self.Role.SUPPORT_MANAGER, self.Role.ADMIN,
            self.Role.PROJECT_MANAGER,
        )

    @property
    def app_language(self):
        """The phone app's language: their country's (Arabic in Egypt and Saudi
        Arabia, English elsewhere — the same as its visit reports), not a choice
        of their own. The website keeps their own `language`."""
        return self.country.report_language if self.country_id else 'en'

    @property
    def is_field_staff(self):
        """Who does the visits — technicians and supervisors: they have their
        own progress and skills (website menu, the app's Progress tab)."""
        return self.role in (self.Role.TECHNICIAN, self.Role.SUPERVISOR)

    @property
    def reaches_all_countries(self):
        """Who works across every country (docs/access_rules.md): another
        country's tasks, customers and people, the all-countries boards, the
        company-wide report and the shared lists (brands, countries, skills).
        A Manager is one per country, so stays in their own."""
        return self.role in (
            self.Role.PROJECT_MANAGER, self.Role.SUPPORT_MANAGER, self.Role.OPERATIONS_MANAGER, self.Role.ADMIN,
        )


    @property
    def is_any_manager(self):
        """Every manager role, not just the manager tier — who can see,
        open and convert any internal ticket in their country.
        """
        return self.role in (
            self.Role.MANAGER, self.Role.SUPPORT_MANAGER, self.Role.COUNTRY_SUPPORT,
            self.Role.WAREHOUSE_MANAGER, self.Role.OPERATIONS_MANAGER, self.Role.ADMIN, self.Role.PROJECT_MANAGER,
        )

    @property
    def escalates_to_role(self):
        """The one role this person escalates a ticket to — a strict chain,
        no skipping levels: Country Support Manager -> Technical Support
        Manager -> Operations Manager, who decides. None at the top.
        """
        return ESCALATION_CHAIN.get(self.role)

    def get_role_display(self):
        """The role's name: a custom role's own, or the built-in one's."""
        if self.custom_role_id:
            return self.custom_role.display_name
        return str(dict(self.Role.choices).get(self.role, self.role))

    @property
    def permission_role(self):
        """Whose switches (RolePermission rows) are this person's: their custom
        role's, or their built-in role's."""
        return self.custom_role.key if self.custom_role_id else self.role

    def has_permission(self, permission):
        return RolePermission.objects.filter(role=self.permission_role, permission=permission, allowed=True).exists()

    @property
    def title(self):
        """What to call them: their job title, or their role's name without one."""
        return self.job_title or self.get_role_display()

    def set_active(self, is_active, reason=''):
        """The one place is_active ever changes — keeps the linked login
        account (if any) in lockstep, so a deactivated technician can't
        just log back in. Clears the reason on reactivation; it only ever
        describes the most recent deactivation.
        """
        self.is_active = is_active
        self.deactivation_reason = reason if not is_active else ''
        self.save(update_fields=['is_active', 'deactivation_reason'])
        if self.user_id is not None and self.user.is_active != is_active:
            self.user.is_active = is_active
            self.user.save(update_fields=['is_active'])


class CustomRole(models.Model):
    """A role the Admin made on Roles & permissions — "COO", "General Manager"
    — based on a built-in role (`based_on`, any but Admin): its people follow
    that role's fixed rules in the code, and its own switches (RolePermission
    rows under `key`), which start as the base role's and the Admin then
    changes (docs/access_rules.md).
    """

    name = models.CharField(_('name'), max_length=60, unique=True)
    name_ar = models.CharField(_('name (Arabic)'), max_length=60, blank=True)
    based_on = models.CharField(
        _('based on'), max_length=20,
        choices=[choice for choice in Technician.Role.choices if choice[0] != Technician.Role.ADMIN],
    )
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)

    class Meta:
        verbose_name = _('custom role')
        verbose_name_plural = _('custom roles')
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def key(self):
        """Its switches' role key in RolePermission."""
        return f'custom-{self.pk}'

    @property
    def display_name(self):
        from django.utils.translation import get_language

        return self.name_ar if self.name_ar and (get_language() or '').startswith('ar') else self.name


def with_permission(permission):
    """A Q for the people whose own switches allow `permission`: built-in roles
    by role, custom roles by their key."""
    from django.db.models import Q

    keys = list(RolePermission.objects.filter(permission=permission, allowed=True).values_list('role', flat=True))
    builtin = [key for key in keys if not key.startswith('custom-')]
    custom = [int(key.split('-', 1)[1]) for key in keys if key.startswith('custom-')]
    return Q(custom_role__isnull=True, role__in=builtin) | Q(custom_role_id__in=custom)


ESCALATION_CHAIN = {
    Technician.Role.COUNTRY_SUPPORT: Technician.Role.SUPPORT_MANAGER,
    Technician.Role.SUPPORT_MANAGER: Technician.Role.OPERATIONS_MANAGER,
}


class RolePermission(models.Model):
    """Which role can do what — configurable, not hardcoded. Each row is
    one (role, permission) pair; `allowed` is the only thing an admin
    changes. Deliberately narrow: it only covers the supervisor-side
    actions that plausibly differ by role (dashboard, tasks, technicians,
    reports). Self-service technician screens (My schedule, My skills, ...)
    stay open to any signed-in technician regardless of role — there's no
    real case yet for excluding a role from their own record.
    """

    class Permission(models.TextChoices):
        VIEW_DASHBOARD = 'view_dashboard', _('View dashboard')
        VIEW_TASKS = 'view_tasks', _('View task list, task detail, and week view')
        CREATE_TASKS = 'create_tasks', _('Create new tasks')
        ASSIGN_TASKS = 'assign_tasks', _('Assign technicians to tasks')
        VIEW_TECHNICIANS = 'view_technicians', _('View technician roster and boards')
        REVIEW_SKILLS = 'review_skills', _('Confirm technician skill levels')
        MANAGE_TICKETS = 'manage_tickets', _('Review customer-submitted tickets')
        MANAGE_TECHNICIANS = 'manage_technicians', _("Edit technicians' details and profile photos")
        # Seeing customers (to plan the work) apart from changing them: a supervisor sees, doesn't edit.
        VIEW_CUSTOMERS = 'view_customers', _('View customers and sites')
        MANAGE_CUSTOMERS = 'manage_customers', _('Edit customers, sites and machines')
        VIEW_MACHINES = 'view_machines', _('View machines and their task/ticket history')
        DECIDE_ESCALATED_TICKETS = 'decide_escalated_tickets', _('Decide tickets escalated to them')
        MANAGE_PARTS = 'manage_parts', _('Maintain the parts catalogue')
        OPEN_TICKETS = 'open_tickets', _('Open tickets for a customer (phone, WhatsApp, email)')
        TICKETS_ALL_COUNTRIES = 'tickets_all_countries', _('Handle tickets in all countries')
        CUSTOMER_ACCOUNTS = 'customer_accounts', _('Manage customer portal accounts')
        VIEW_STOCK = 'view_stock', _('View warehouse stock')

    # A built-in role's code, or a custom role's key ("custom-7": CustomRole.key).
    role = models.CharField(_('role'), max_length=20)
    permission = models.CharField(_('permission'), max_length=30, choices=Permission.choices)
    allowed = models.BooleanField(_('allowed'), default=False)

    class Meta:
        verbose_name = _('role permission')
        verbose_name_plural = _('role permissions')
        ordering = ['permission', 'role']
        constraints = [
            models.UniqueConstraint(fields=['role', 'permission'], name='unique_role_permission'),
        ]

    def __str__(self):
        return f'{self.get_role_display()} — {self.get_permission_display()}: {self.allowed}'


def _default_new_ticket_roles():
    # Told of each new ticket, by email and in the bell: the Technical Support Manager
    # (every country) and each country's Country Support Manager (their own). Nobody of
    # these for a country: the admins instead (tasks.views.new_ticket_recipients).
    return ['support_manager', 'country_support']


def _default_new_task_roles():
    return ['manager', 'admin']


class NotificationSettings(models.Model):
    """A single row: which automatic messages the system sends, and to
    whom — set by an admin on the Notifications page (tasks.views.
    notification_settings). Everything is on by default. Messages a person
    sends with a button (running late, feedback request, ...) and the
    security emails (password changed, set-up links) have no switch.
    """

    # To customers.
    ticket_received_email = models.BooleanField(
        _('email the customer when their ticket is received'), default=True,
    )
    reply_to_customer_email = models.BooleanField(
        _('email the customer when the office replies to their ticket'), default=True,
    )
    auto_notify_on_reschedule = models.BooleanField(
        _('email the customer automatically when a task is rescheduled'), default=True,
        help_text=_('when off, a supervisor sends it by hand from the task details instead'),
    )
    # To staff.
    new_ticket_email = models.BooleanField(_('email staff about a new customer ticket'), default=True)
    new_ticket_roles = ArrayField(
        models.CharField(max_length=20), default=_default_new_ticket_roles, blank=True,
        verbose_name=_('roles emailed about a new customer ticket'),
    )
    new_task_email = models.BooleanField(_('email staff about a new task'), default=True)
    new_task_roles = ArrayField(
        models.CharField(max_length=20), default=_default_new_task_roles, blank=True,
        verbose_name=_('roles emailed about a new task'),
    )
    reply_to_staff_email = models.BooleanField(
        _('email the assigned person when a customer replies to a ticket'), default=True,
    )
    escalation_emails = models.BooleanField(
        _('email about ticket escalations and their decisions'), default=True,
    )
    shipment_to_supervisor_email = models.BooleanField(
        _("email the task's supervisor when parts are shipped"), default=True,
    )
    # To technicians' phones.
    push_assigned = models.BooleanField(_('phone notification when assigned to a task'), default=True)
    push_removed = models.BooleanField(_('phone notification when taken off a task'), default=True)
    push_cancelled = models.BooleanField(_('phone notification when a task is cancelled'), default=True)

    class Meta:
        verbose_name = _('notification settings')
        verbose_name_plural = _('notification settings')

    @classmethod
    def load(cls):
        """The one row this table ever has — created on first use."""
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return str(_('Notification settings'))


class TechnicianTrip(models.Model):
    """A technician working in another country for set dates (docs:
    technician_trip) — assignable there during the trip, while their own
    country, roster, figures and hours stay at home.
    """

    technician = models.ForeignKey(
        Technician, on_delete=models.CASCADE, related_name='trips', verbose_name=_('technician'),
    )
    country = models.ForeignKey(
        Country, on_delete=models.PROTECT, related_name='visiting_trips', verbose_name=_('country'),
    )
    start_date = models.DateField(_('start date'))
    end_date = models.DateField(_('end date'))
    note = models.CharField(_('note'), max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='trips_recorded',
        verbose_name=_('recorded by'),
    )
    created_at = models.DateTimeField(_('created at'))

    class Meta:
        verbose_name = _('trip')
        verbose_name_plural = _('trips')
        ordering = ['-start_date']

    def __str__(self):
        return f'{self.technician} — {self.country} ({self.start_date} to {self.end_date})'


class TechnicianSkill(models.Model):
    """The current level snapshot. `TechnicianSkillAssessment` holds the
    full history of self-ratings and supervisor reviews behind it.
    """

    class Source(models.TextChoices):
        SELF = 'self', _('Self-rated')
        SUPERVISOR = 'supervisor', _('Supervisor')

    technician = models.ForeignKey(
        Technician, on_delete=models.CASCADE, related_name='skills',
        verbose_name=_('technician'),
    )
    skill = models.ForeignKey(
        Skill, on_delete=models.PROTECT, related_name='technician_skills',
        verbose_name=_('skill'),
    )
    level = models.PositiveSmallIntegerField(_('level'), choices=SKILL_LEVEL_CHOICES)
    source = models.CharField(
        _('source'), max_length=10, choices=Source.choices,
        help_text=_(
            'a self-assessment is an initial estimate; only a supervisor review counts toward certification',
        ),
    )
    set_by = models.ForeignKey(
        Technician, on_delete=models.PROTECT, related_name='levels_set',
        verbose_name=_('set by'),
        help_text=_('the technician for a self-assessment; the supervisor for a review'),
    )
    set_on = models.DateField(_('set on'))
    note = models.CharField(_('note'), max_length=255, blank=True)
    evidence = models.FileField(
        _('evidence'), upload_to=_skill_evidence_path, null=True, blank=True,
        validators=[SafeFileValidator(allowed_extensions=ALLOWED_SKILL_EVIDENCE_EXTENSIONS)],
        help_text=_('photo or video of the technician performing this repair'),
    )

    class Meta:
        verbose_name = _('technician skill')
        verbose_name_plural = _('technician skills')
        ordering = ['technician__full_name', 'skill__name']
        constraints = [
            models.UniqueConstraint(
                fields=['technician', 'skill'], name='unique_technician_skill',
            ),
        ]

    def __str__(self):
        return f'{self.technician.full_name} — {self.skill} ({self.level})'


class TechnicianSkillAssessment(models.Model):
    """Append-only log of every self-rating and supervisor review. Never
    edited or deleted — `TechnicianSkill` is just the latest entry's snapshot,
    kept separately so it stays a single fast row per (technician, skill).
    """

    technician = models.ForeignKey(
        Technician, on_delete=models.CASCADE, related_name='skill_assessments',
        verbose_name=_('technician'),
    )
    skill = models.ForeignKey(
        Skill, on_delete=models.PROTECT, related_name='technician_assessments',
        verbose_name=_('skill'),
    )
    level = models.PositiveSmallIntegerField(_('level'), choices=SKILL_LEVEL_CHOICES)
    source = models.CharField(_('source'), max_length=10, choices=TechnicianSkill.Source.choices)
    set_by = models.ForeignKey(
        Technician, on_delete=models.PROTECT, related_name='skill_assessments_made',
        verbose_name=_('set by'),
        help_text=_('the technician for a self-assessment; the supervisor for a review'),
    )
    set_on = models.DateField(_('set on'))
    note = models.CharField(_('note'), max_length=255, blank=True)
    evidence = models.FileField(
        _('evidence'), upload_to=_skill_evidence_path, null=True, blank=True,
        validators=[SafeFileValidator(allowed_extensions=ALLOWED_SKILL_EVIDENCE_EXTENSIONS)],
        help_text=_('photo or video of the technician performing this repair'),
    )

    class Meta:
        verbose_name = _('technician skill assessment')
        verbose_name_plural = _('technician skill assessments')
        ordering = ['-set_on', '-id']

    def __str__(self):
        return f'{self.technician.full_name} — {self.skill} ({self.level}, {self.get_source_display()})'


class TechnicianConduct(models.Model):
    """Current level snapshot for a non-technical conduct area (cleanliness,
    procedure adherence, ...) — the professionalism half of the
    certification bar, tracked the same way as `TechnicianSkill` but never
    tied to a brand. `TechnicianConductAssessment` holds its history.
    """

    class Source(models.TextChoices):
        SELF = 'self', _('Self-rated')
        SUPERVISOR = 'supervisor', _('Supervisor')

    technician = models.ForeignKey(
        Technician, on_delete=models.CASCADE, related_name='conduct_ratings',
        verbose_name=_('technician'),
    )
    conduct_area = models.ForeignKey(
        ConductArea, on_delete=models.PROTECT, related_name='technician_ratings',
        verbose_name=_('conduct area'),
    )
    level = models.PositiveSmallIntegerField(_('level'), choices=SKILL_LEVEL_CHOICES)
    source = models.CharField(
        _('source'), max_length=10, choices=Source.choices,
        help_text=_(
            'a self-assessment is an initial estimate; only a supervisor review counts toward certification',
        ),
    )
    set_by = models.ForeignKey(
        Technician, on_delete=models.PROTECT, related_name='conduct_ratings_set',
        verbose_name=_('set by'),
        help_text=_('the technician for a self-assessment; the supervisor for a review'),
    )
    set_on = models.DateField(_('set on'))
    note = models.CharField(_('note'), max_length=255, blank=True)

    class Meta:
        verbose_name = _('technician conduct rating')
        verbose_name_plural = _('technician conduct ratings')
        ordering = ['technician__full_name', 'conduct_area__name']
        constraints = [
            models.UniqueConstraint(
                fields=['technician', 'conduct_area'], name='unique_technician_conduct',
            ),
        ]

    def __str__(self):
        return f'{self.technician.full_name} — {self.conduct_area} ({self.level})'


class TechnicianConductAssessment(models.Model):
    """Append-only log of every self-rating and supervisor review for a
    conduct area — mirrors `TechnicianSkillAssessment`.
    """

    technician = models.ForeignKey(
        Technician, on_delete=models.CASCADE, related_name='conduct_assessments',
        verbose_name=_('technician'),
    )
    conduct_area = models.ForeignKey(
        ConductArea, on_delete=models.PROTECT, related_name='technician_assessments',
        verbose_name=_('conduct area'),
    )
    level = models.PositiveSmallIntegerField(_('level'), choices=SKILL_LEVEL_CHOICES)
    source = models.CharField(_('source'), max_length=10, choices=TechnicianConduct.Source.choices)
    set_by = models.ForeignKey(
        Technician, on_delete=models.PROTECT, related_name='conduct_assessments_made',
        verbose_name=_('set by'),
        help_text=_('the technician for a self-assessment; the supervisor for a review'),
    )
    set_on = models.DateField(_('set on'))
    note = models.CharField(_('note'), max_length=255, blank=True)

    class Meta:
        verbose_name = _('technician conduct assessment')
        verbose_name_plural = _('technician conduct assessments')
        ordering = ['-set_on', '-id']

    def __str__(self):
        return (
            f'{self.technician.full_name} — {self.conduct_area} '
            f'({self.level}, {self.get_source_display()})'
        )


class PushDevice(models.Model):
    """A phone that receives push notifications for this technician
    (docs: push_device) — registered by the mobile app after sign-in.
    """

    technician = models.ForeignKey(
        Technician, on_delete=models.CASCADE, related_name='push_devices', verbose_name=_('technician'),
    )
    token = models.CharField(_('push token'), max_length=200, unique=True)
    created_at = models.DateTimeField(_('created at'))

    class Meta:
        verbose_name = _('push device')
        verbose_name_plural = _('push devices')

    def __str__(self):
        return f'{self.technician} — {self.token[:24]}'


class AppError(models.Model):
    """Something that went wrong in the phone app — a screen that crashed, an
    error nobody caught — sent by the app itself, so a failure on a
    technician's phone in the field reaches the office instead of being lost.
    The admins see how many are new in their bell; the list is in the Django
    admin (opening it marks them seen)."""

    technician = models.ForeignKey(
        'Technician', on_delete=models.SET_NULL, null=True, blank=True, related_name='app_errors',
        verbose_name=_('technician'),
    )
    message = models.TextField(_('message'))
    stack = models.TextField(_('stack'), blank=True)
    screen = models.CharField(_('screen'), max_length=200, blank=True)
    app_version = models.CharField(_('app version'), max_length=40, blank=True)
    platform = models.CharField(_('platform'), max_length=40, blank=True)
    occurred_at = models.DateTimeField(_('occurred at'))
    seen_at = models.DateTimeField(_('seen at'), null=True, blank=True)

    class Meta:
        verbose_name = _('app error')
        verbose_name_plural = _('app errors')
        ordering = ['-occurred_at']

    def __str__(self):
        return f'{self.occurred_at:%Y-%m-%d %H:%M} — {self.message[:80]}'


class FailedEmail(models.Model):
    """An email the app tried and failed to send (the mail server refused or
    couldn't be reached) — emails go out in the background, so the person
    who caused it would otherwise never know. Shown once, on their next
    page, to the staff member whose action sent it; with nobody behind it (a
    customer's own request, say), to the admins (spots.email_failures)."""

    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='failed_emails', verbose_name=_('triggered by'),
    )
    recipients = models.TextField(_('recipients'))
    subject = models.CharField(_('subject'), max_length=255)
    error = models.TextField(_('error'))
    failed_at = models.DateTimeField(_('failed at'))
    seen_at = models.DateTimeField(_('seen at'), null=True, blank=True)

    class Meta:
        verbose_name = _('failed email')
        verbose_name_plural = _('failed emails')
        ordering = ['-failed_at']

    def __str__(self):
        return f'{self.subject} → {self.recipients}'
