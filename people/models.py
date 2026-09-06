from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from reference.models import Country, Skill


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

    class Meta:
        verbose_name = _('technician')
        verbose_name_plural = _('technicians')
        ordering = ['full_name']

    def __str__(self):
        return self.full_name


class TechnicianSkill(models.Model):
    technician = models.ForeignKey(
        Technician, on_delete=models.CASCADE, related_name='skills',
        verbose_name=_('technician'),
    )
    skill = models.ForeignKey(
        Skill, on_delete=models.PROTECT, related_name='technician_skills',
        verbose_name=_('skill'),
    )
    level = models.PositiveSmallIntegerField(
        _('level'), choices=[(i, str(i)) for i in range(1, 5)],
    )
    set_by = models.ForeignKey(
        Technician, on_delete=models.PROTECT, related_name='levels_set',
        verbose_name=_('set by'),
        help_text=_("which supervisor decided"),
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
