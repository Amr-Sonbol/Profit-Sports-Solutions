from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.utils.translation import gettext_lazy as _

from reference.models import Country
from tasks.location import parse_coordinates

from .models import Customer, Site


class CreateLoginForm(forms.Form):
    """Just an email — it becomes both the login username and the address
    the welcome link is sent to (accounts.send_welcome), one thing for the
    customer to remember instead of a separate invented username. The
    customer sets their own password through that link.
    """
    email = forms.EmailField(label=_('Email'))

    def clean_email(self):
        email = self.cleaned_data['email']
        if get_user_model().objects.filter(username__iexact=email).exists():
            raise forms.ValidationError(_('An account with this email address already exists.'))
        return email


class CustomerFirstLoginForm(forms.Form):
    """Shown once, when a customer first opens their account (the welcome
    link, or a temporary password) — they set their own password and
    complete the company details staff may not have had when the account
    was created (a bulk import, say). Prefilled with whatever's already
    on file.
    """
    contact_name = forms.CharField(max_length=150, label=_('Contact name'))
    contact_phone = forms.CharField(max_length=30, label=_('Contact phone number'))
    contact_email = forms.EmailField(label=_('Contact email'))
    billing_address = forms.CharField(
        label=_('Invoice address'), widget=forms.Textarea(attrs={'rows': 3}),
        help_text=_('The address invoices should be issued to.'),
    )
    shipping_address = forms.CharField(
        label=_('Delivery address'), widget=forms.Textarea(attrs={'rows': 3}),
        help_text=_('Where spare and replacement parts should be delivered.'),
    )
    new_password1 = forms.CharField(widget=forms.PasswordInput, label=_('New password'))
    new_password2 = forms.CharField(widget=forms.PasswordInput, label=_('Confirm new password'))

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_new_password2(self):
        password1 = self.cleaned_data.get('new_password1')
        password2 = self.cleaned_data.get('new_password2')
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError(_('The two password fields do not match.'))
        if password1:
            password_validation.validate_password(password1, self.user)
        return password2


class CustomerCreateForm(forms.ModelForm):
    """Country comes from the supervisor creating it, set in the view —
    never a field here, same scoping every other per-country screen uses.
    """

    class Meta:
        model = Customer
        fields = ['name', 'segment']


class CustomerEditForm(forms.ModelForm):
    """Everything about an existing customer that isn't country (that's
    a relocation, not an edit — no such action exists yet for a customer,
    same as it took a deliberate one for a technician).
    """

    class Meta:
        model = Customer
        fields = [
            'name', 'code', 'segment', 'language',
            'contact_name', 'contact_phone', 'contact_email', 'billing_address', 'shipping_address',
        ]
        widgets = {
            'billing_address': forms.Textarea(attrs={'rows': 2}),
            'shipping_address': forms.Textarea(attrs={'rows': 2}),
        }


class DeactivateCustomerForm(forms.Form):
    """Permanent, unlike toggling one site inactive — contract ended,
    business closed, etc. Free text rather than fixed choices, same as
    DeactivateTechnicianForm — a one-off note, not reported on.
    """
    reason = forms.CharField(label=_('Reason'), widget=forms.Textarea(attrs={'rows': 2}))


class SiteCreateForm(forms.ModelForm):
    """A branch/location under an existing customer. The customer itself
    is set in the view (passed in as `customer`, not a field here) — used
    to check for a same-named site under that customer, same rule
    task_create's inline "new site" fields already enforce.
    """

    class Meta:
        model = Site
        fields = ['name', 'address', 'contact_name', 'contact_phone', 'contact_email', 'access_notes']
        widgets = {
            'address': forms.Textarea(attrs={'rows': 2}),
            'access_notes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, customer=None, **kwargs):
        self.customer = customer
        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = self.cleaned_data['name']
        if self.customer and Site.objects.filter(customer=self.customer, name__iexact=name).exists():
            raise forms.ValidationError(_('This customer already has a site with this name.'))
        return name


class SiteEditForm(forms.ModelForm):
    """Same fields as SiteCreateForm, for a site that already exists —
    contact fields left blank here fall back to the customer's own
    (Site.effective_contact_*), so a shared or single-branch contact
    only needs to live in one place.
    """

    # Where the site is on the map, which technicians' taps are checked
    # against. Coordinates or a full Google Maps link; empty clears it.
    map_location = forms.CharField(
        required=False, label=_('Map location'),
        help_text=_('Paste coordinates (e.g. 25.0772, 55.1306) or a full Google Maps link. If you leave it '
                    "blank, it's set when the first technician taps “Arrived” here."),
    )

    class Meta:
        model = Site
        fields = ['name', 'address', 'contact_name', 'contact_phone', 'contact_email', 'access_notes']
        widgets = {
            'address': forms.Textarea(attrs={'rows': 2}),
            'access_notes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.latitude is not None:
            self.fields['map_location'].initial = f'{self.instance.latitude}, {self.instance.longitude}'
            if self.instance.location_source == Site.LocationSource.ARRIVAL:
                self.fields['map_location'].help_text = _(
                    "Set by the first technician to arrive. Check it and fix it if it's wrong.",
                )

    def clean_name(self):
        name = self.cleaned_data['name']
        if Site.objects.filter(
            customer=self.instance.customer, name__iexact=name,
        ).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_('This customer already has a site with this name.'))
        return name

    def clean_map_location(self):
        text = self.cleaned_data['map_location'].strip()
        if not text:
            return None
        coordinates = parse_coordinates(text)
        if coordinates is None:
            raise forms.ValidationError(_(
                "Couldn't find coordinates. Enter them as “latitude, longitude” or paste a full Google Maps "
                'link (not a maps.app.goo.gl short link).',
            ))
        return coordinates

    def save(self, commit=True):
        site = super().save(commit=False)
        coordinates = self.cleaned_data.get('map_location')
        unchanged = coordinates == (site.latitude, site.longitude)
        if coordinates is None:
            site.latitude = site.longitude = None
            site.location_source = ''
        elif not unchanged:
            site.latitude, site.longitude = coordinates
            site.location_source = Site.LocationSource.OFFICE
        if commit:
            site.save()
        return site


class CustomerImportForm(forms.Form):
    """One row per site — see the downloadable template for columns.
    Rows sharing the same customer_name become one Customer with
    several Sites, covering a chain in one file instead of one row.
    """
    csv_file = forms.FileField(label=_('CSV file'))

    def clean_csv_file(self):
        csv_file = self.cleaned_data['csv_file']
        if not csv_file.name.lower().endswith('.csv'):
            raise forms.ValidationError(_('Upload a .csv file.'))
        return csv_file


class ChangeLoginEmailForm(forms.Form):
    email = forms.EmailField(label=_('New login email'))

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data['email']
        taken = get_user_model().objects.filter(username__iexact=email)
        if self.user is not None:
            taken = taken.exclude(pk=self.user.pk)
        if taken.exists():
            raise forms.ValidationError(_('An account with this email address already exists.'))
        return email


class PortalPasswordHelpForm(forms.Form):
    email = forms.EmailField(label=_('Your login email'))


class QuickInviteForm(forms.Form):
    """A new customer from just a name and an email, invited straight
    away; they complete everything else through the welcome link. Country
    and segment are chosen every time — never pre-filled — so nobody
    files a customer under the wrong country by leaving a default alone.
    """
    name = forms.CharField(max_length=150, label=_('Customer name'))
    email = forms.EmailField(label=_('Email'))
    country = forms.ModelChoiceField(
        queryset=Country.objects.filter(is_active=True).order_by('name'), label=_('Country'),
        empty_label=_('Select a country'),
    )
    segment = forms.ChoiceField(
        choices=[('', _('Select a segment'))] + list(Customer.Segment.choices), label=_('Segment'),
    )

    def clean_email(self):
        email = self.cleaned_data['email']
        if get_user_model().objects.filter(username__iexact=email).exists():
            raise forms.ValidationError(_('An account with this email address already exists.'))
        return email

    def clean(self):
        cleaned = super().clean()
        name, country = cleaned.get('name'), cleaned.get('country')
        if name and country and Customer.objects.filter(name__iexact=name.strip(), country=country).exists():
            raise forms.ValidationError(_(
                'A customer named %(name)s already exists in this country. Send the invitation from their row '
                'instead.',
            ) % {'name': name.strip()})
        return cleaned
