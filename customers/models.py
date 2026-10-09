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
    must_change_password = models.BooleanField(
        _('must change password'), default=False,
        help_text=_(
            'set when staff creates the login with a temporary system-generated password — the '
            'customer is walked through setting their own password and confirming their contact '
            'details once, the first time they sign in, before reaching the rest of the portal',
        ),
    )
    is_active = models.BooleanField(_('active'), default=True)
    deactivation_reason = models.CharField(
        _('deactivation reason'), max_length=255, blank=True,
        help_text=_('why this customer was deactivated — contract ended, closed down, etc.'),
    )

    class Meta:
        verbose_name = _('customer')
        verbose_name_plural = _('customers')
        ordering = ['name']

    def __str__(self):
        return self.name

    def set_active(self, is_active, reason=''):
        """The one place is_active ever changes — keeps the linked login
        (if any) in lockstep, so a deactivated customer can't just log
        back in. Same pattern as Technician.set_active. Never deletes the
        record itself — a deactivated customer keeps its history (sites,
        tasks, tickets), it just drops off the active roster.
        """
        self.is_active = is_active
        self.deactivation_reason = reason if not is_active else ''
        self.save(update_fields=['is_active', 'deactivation_reason'])
        if self.user_id is not None and self.user.is_active != is_active:
            self.user.is_active = is_active
            self.user.save(update_fields=['is_active'])


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

    class LocationSource(models.TextChoices):
        OFFICE = 'office', _('Entered by the office')
        ARRIVAL = 'arrival', _('Set by the first arrival')

    # Where the site is on the map — what a technician's taps are checked
    # against (tasks.location). Entered by the office, or taken from the
    # first "Arrived" tap when nobody has.
    latitude = models.DecimalField(_('latitude'), max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(_('longitude'), max_digits=9, decimal_places=6, null=True, blank=True)
    location_source = models.CharField(
        _('location source'), max_length=10, choices=LocationSource.choices, blank=True,
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
