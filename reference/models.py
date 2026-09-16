from django.db import models
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _


class Country(models.Model):
    name = models.CharField(_('name'), max_length=100)
    name_ar = models.CharField(_('name (Arabic)'), max_length=100)
    iso_code = models.CharField(_('ISO code'), max_length=2)
    timezone = models.CharField(
        _('timezone'), max_length=50,
        help_text=_('IANA name, e.g. Asia/Riyadh'),
    )
    currency_code = models.CharField(_('currency code'), max_length=3)
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        verbose_name = _('country')
        verbose_name_plural = _('countries')
        ordering = ['name']

    def __str__(self):
        return self.name


class Brand(models.Model):
    name = models.CharField(_('name'), max_length=100)
    portal_url = models.URLField(
        _('portal URL'), blank=True,
        help_text=_('where the office requests spare parts'),
    )
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        verbose_name = _('brand')
        verbose_name_plural = _('brands')
        ordering = ['name']

    def __str__(self):
        return self.name


class Skill(models.Model):
    class Category(models.TextChoices):
        OTHER = 'other', _('Other')
        CARDIO = 'cardio', _('Cardio')

    brand = models.ForeignKey(
        Brand, on_delete=models.PROTECT, related_name='skills',
        verbose_name=_('brand'),
    )
    name = models.CharField(_('name'), max_length=100)
    category = models.CharField(
        _('category'), max_length=10, choices=Category.choices, default=Category.OTHER,
        help_text=_("cardio lines don't count toward the technician certification bar"),
    )
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        verbose_name = _('skill')
        verbose_name_plural = _('skills')
        ordering = ['brand__name', 'name']

    def __str__(self):
        return f'{self.brand.name} — {self.name}'


class ConductArea(models.Model):
    """A non-technical professionalism area every technician is rated on —
    cleanliness, procedure adherence, etc. Not tied to any brand, and part
    of the certification bar alongside `Skill` (see people.TechnicianConduct).
    """
    name = models.CharField(_('name'), max_length=100)
    name_ar = models.CharField(_('name (Arabic)'), max_length=100)
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        verbose_name = _('conduct area')
        verbose_name_plural = _('conduct areas')
        ordering = ['name']

    @property
    def display_name(self):
        return self.name_ar if get_language() == 'ar' else self.name

    def __str__(self):
        return self.display_name


class TaskType(models.Model):
    class Category(models.TextChoices):
        INSTALLATION = 'installation', _('Installation')
        MAINTENANCE = 'maintenance', _('Maintenance')

    code = models.CharField(
        _('code'), max_length=50, unique=True,
        help_text=_('stable key, never changes'),
    )
    name = models.CharField(_('name'), max_length=100)
    name_ar = models.CharField(_('name (Arabic)'), max_length=100)
    category = models.CharField(
        _('category'), max_length=20, choices=Category.choices,
    )
    requires_photos = models.BooleanField(_('requires photos'), default=False)
    requires_signature = models.BooleanField(_('requires signature'), default=False)
    checklist_template = models.JSONField(
        _('checklist template'), default=dict, blank=True,
        help_text=_('fields the report form shows for this type'),
    )
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        verbose_name = _('task type')
        verbose_name_plural = _('task types')
        ordering = ['name']

    @property
    def display_name(self):
        return self.name_ar if get_language() == 'ar' else self.name

    def __str__(self):
        return self.display_name
