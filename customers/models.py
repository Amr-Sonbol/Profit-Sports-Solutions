from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from people.models import Technician
from reference.models import Brand, Country


class Customer(models.Model):
    class Segment(models.TextChoices):
        GYM = 'gym', _('Gym')
        HOTEL = 'hotel', _('Hotel')
        CLUB = 'club', _('Club')
        OTHER = 'other', _('Other')

    country = models.ForeignKey(
        Country, on_delete=models.PROTECT, related_name='customers',
        verbose_name=_('country'),
    )
    name = models.CharField(_('name'), max_length=150)
    code = models.CharField(
        _('customer code'), max_length=50, blank=True,
        help_text=_('this company’s account/reference code, if it has one'),
    )
    segment = models.CharField(_('segment'), max_length=20, choices=Segment.choices)
    language = models.CharField(
        _('language'), max_length=2, choices=Technician.Language.choices, default=Technician.Language.EN,
        help_text=_('for the portal login, if this customer has one'),
    )
    contact_name = models.CharField(_('contact name'), max_length=150, blank=True)
    contact_phone = models.CharField(_('contact phone'), max_length=30, blank=True)
    contact_email = models.EmailField(
        _('contact email'), blank=True,
        help_text=_(
            'used for a site that has no contact of its own — the single-branch case, or a '
            'chain where every site shares the same one'
        ),
    )
    shipping_address = models.TextField(
        _('shipping address'), blank=True,
        help_text=_('where replacement parts should be delivered, if different from the site itself — a warehouse or head office'),
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='customer', verbose_name=_('login account'),
        help_text=_('one account covers every site under this customer — created by staff, never self-signup'),
    )
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        verbose_name = _('customer')
        verbose_name_plural = _('customers')
        ordering = ['name']

    def __str__(self):
        return self.name


class Site(models.Model):
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name='sites',
        verbose_name=_('customer'),
    )
    name = models.CharField(_('name'), max_length=150)
    address = models.TextField(_('address'))
    contact_name = models.CharField(_('contact name'), max_length=150, blank=True)
    contact_phone = models.CharField(_('contact phone'), max_length=30, blank=True)
    contact_email = models.EmailField(
        _('contact email'), blank=True,
        help_text=_('where a feedback request goes after a report is approved'),
    )
    access_notes = models.TextField(
        _('access notes'), blank=True,
        help_text=_('gate codes, best hours'),
    )

    class Meta:
        verbose_name = _('site')
        verbose_name_plural = _('sites')
        ordering = ['customer__name', 'name']

    def __str__(self):
        return f'{self.customer.name} — {self.name}'

    @property
    def effective_contact_name(self):
        """This site's own contact if it has one, else the customer's —
        covers a single-branch customer, or a chain where every site
        shares one contact and nobody wants to re-enter it per site.
        """
        return self.contact_name or self.customer.contact_name

    @property
    def effective_contact_phone(self):
        return self.contact_phone or self.customer.contact_phone

    @property
    def effective_contact_email(self):
        return self.contact_email or self.customer.contact_email

    @property
    def has_own_contact(self):
        """False when this row's contact is inherited from the customer
        rather than set on the site itself — for a "from customer" badge.
        """
        return bool(self.contact_name or self.contact_phone or self.contact_email)


class Asset(models.Model):
    """One physical machine. Created by the technician at the first service visit."""

    class Status(models.TextChoices):
        ACTIVE = 'active', _('Active')
        FAULTY = 'faulty', _('Faulty')
        RETIRED = 'retired', _('Retired')

    site = models.ForeignKey(
        Site, on_delete=models.PROTECT, related_name='assets',
        verbose_name=_('site'),
    )
    brand = models.ForeignKey(
        Brand, on_delete=models.PROTECT, related_name='assets',
        verbose_name=_('brand'),
    )
    model_name = models.CharField(
        _('model name'), max_length=150, help_text=_('free text from the plate'),
    )
    serial_no = models.CharField(_('serial number'), max_length=100, blank=True)
    installed_on = models.DateField(_('installed on'), null=True, blank=True)
    warranty_end = models.DateField(_('warranty end'), null=True, blank=True)
    status = models.CharField(_('status'), max_length=20, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        verbose_name = _('asset')
        verbose_name_plural = _('assets')
        ordering = ['site__name', 'model_name']
        indexes = [
            models.Index(fields=['site', 'status']),
        ]

    def __str__(self):
        return f'{self.site.name} — {self.brand.name} {self.model_name}'
