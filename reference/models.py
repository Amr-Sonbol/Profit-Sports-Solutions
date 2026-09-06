from django.db import models
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
    brand = models.ForeignKey(
        Brand, on_delete=models.PROTECT, related_name='skills',
        verbose_name=_('brand'),
    )
    name = models.CharField(_('name'), max_length=100)
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        verbose_name = _('skill')
        verbose_name_plural = _('skills')
        ordering = ['brand__name', 'name']

    def __str__(self):
        return f'{self.brand.name} — {self.name}'


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

    def __str__(self):
        return self.name
