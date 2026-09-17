from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from reference.models import ConductArea, Country, Skill

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


class Technician(models.Model):
    class Role(models.TextChoices):
        TECHNICIAN = 'technician', _('Technician')
        SUPERVISOR = 'supervisor', _('Supervisor')
        MANAGER = 'manager', _('Manager')

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
        help_text=_('for Microsoft SSO later'),
    )
    country = models.ForeignKey(
        Country, on_delete=models.PROTECT, related_name='technicians',
        verbose_name=_('country'),
    )
    full_name = models.CharField(_('full name'), max_length=150)
    phone = models.CharField(_('phone'), max_length=30, blank=True)
    language = models.CharField(
        _('language'), max_length=2, choices=Language.choices,
        help_text=_('per person, not per country'),
    )
    role = models.CharField(_('role'), max_length=20, choices=Role.choices)
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
    is_available = models.BooleanField(
        _('available'), default=True,
        help_text=_('whether this technician can currently be assigned work — separate from is_active'),
    )
    unavailable_reason = models.CharField(
        _('unavailable reason'), max_length=20, choices=UnavailableReason.choices, blank=True,
    )
    photo = models.FileField(
        _('photo'), upload_to='technician_photos/', null=True, blank=True,
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_PHOTO_EXTENSIONS)],
        help_text=_('shown on the roster, boards, and task detail'),
    )

    class Meta:
        verbose_name = _('technician')
        verbose_name_plural = _('technicians')
        ordering = ['full_name']

    def __str__(self):
        return self.full_name


class RolePermission(models.Model):
    """Which role can do what — configurable, not hardcoded. Each row is
    one (role, permission) pair; `allowed` is the only thing an admin
    changes. Deliberately narrow: it only covers the supervisor-side
    actions that plausibly differ by role (dashboard, tasks, technicians,
    reports). Self-service technician screens (My week, My skills, ...)
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
        REVIEW_REPORTS = 'review_reports', _('View and review work reports')
        MANAGE_TICKETS = 'manage_tickets', _('Review customer-submitted tickets')
        MANAGE_TECHNICIANS = 'manage_technicians', _('Edit technician profile photos')
        MANAGE_CUSTOMERS = 'manage_customers', _('Add and view customers and sites')

    role = models.CharField(_('role'), max_length=20, choices=Technician.Role.choices)
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


class NotificationSettings(models.Model):
    """A single row, manager-controlled from the same Roles & permissions
    screen — whether a schedule-change email to the customer happens
    automatically or waits for a supervisor to send it deliberately.
    Off by default: rescheduling never notified anyone by itself before
    this setting existed, and that stays true until a manager turns it on.
    """

    auto_notify_on_reschedule = models.BooleanField(
        _('email the customer automatically when a task is rescheduled'), default=False,
        help_text=_('off by default — a supervisor sends it deliberately from task detail instead'),
    )

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
            'a self-rating is a starting guess — only a supervisor review counts toward certification',
        ),
    )
    set_by = models.ForeignKey(
        Technician, on_delete=models.PROTECT, related_name='levels_set',
        verbose_name=_('set by'),
        help_text=_('the technician himself for a self-rating, the supervisor for a review'),
    )
    set_on = models.DateField(_('set on'))
    note = models.CharField(_('note'), max_length=255, blank=True)

    class Meta:
        verbose_name = _('technician skill')
        verbose_name_plural = _('technician skills')
        ordering = ['technician__full_name', 'skill__brand__name']
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
        help_text=_('the technician himself for a self-rating, the supervisor for a review'),
    )
    set_on = models.DateField(_('set on'))
    note = models.CharField(_('note'), max_length=255, blank=True)

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
            'a self-rating is a starting guess — only a supervisor review counts toward certification',
        ),
    )
    set_by = models.ForeignKey(
        Technician, on_delete=models.PROTECT, related_name='conduct_ratings_set',
        verbose_name=_('set by'),
        help_text=_('the technician himself for a self-rating, the supervisor for a review'),
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
        help_text=_('the technician himself for a self-rating, the supervisor for a review'),
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
