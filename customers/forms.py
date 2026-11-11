import re

from django import forms
from django.db.models import Q
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.forms import AuthenticationForm
from django.utils.translation import gettext_lazy as _

from reference.models import Brand, Country
from tasks.forms import new_password_fields
from tasks.models import MAX_TICKET_ATTACHMENT_BYTES, SERIAL_PHOTO_EXTENSIONS
from tasks.location import parse_coordinates

from .models import Asset, Customer, Site
from spots.uploads import SafeFileValidator


class PortalLoginForm(AuthenticationForm):
    """The portal's login: one plain message for a wrong email, a wrong
    password or a staff login alike, so it never says which accounts exist."""

    error_messages = {
        **AuthenticationForm.error_messages,
        'invalid_login': _('The email or password is incorrect. Please check them and try again.'),
    }


class AddPortalUserForm(forms.Form):
    """One more person at a customer who signs in to the portal: their name,
    and their email — both their username and where the welcome link goes
    (accounts.add_person, send_welcome). They set their own password there.
    """
    name = forms.CharField(max_length=150, label=_('Name'))
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
    new_password1, new_password2 = new_password_fields()

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


class NewCustomerForm(forms.Form):
    """The one way a customer is created (customers.views.customer_create):
    the customer, its first branch, its contact, and — ticked by default —
    the portal invitation, sent to the contact email. The country is chosen
    every time, never pre-filled, so nobody files a customer under the
    wrong country by leaving a default alone — except from "New ticket",
    where it's the ticket's own (`country`).

    A customer of the same name in that country is refused (add a branch to
    it instead); one with a similar name is shown first ("Is it one of
    these?") and needs "It is a new customer" ticked to go ahead.
    """

    name = forms.CharField(max_length=150, label=_('Customer name'))
    segment = forms.ChoiceField(
        choices=[('', _('Select a segment'))] + list(Customer.Segment.choices), label=_('Segment'),
    )
    country = forms.ModelChoiceField(
        queryset=Country.objects.filter(is_active=True).order_by('name'), label=_('Country'),
        empty_label=_('Select a country'),
    )
    branch_name = forms.CharField(
        max_length=150, label=_('Branch name'), help_text=_('For example the area, or the main branch.'),
    )
    branch_address = forms.CharField(label=_('Branch address'), widget=forms.Textarea(attrs={'rows': 2}))
    contact_name = forms.CharField(max_length=150, required=False, label=_('Contact name'))
    contact_phone = forms.CharField(max_length=30, required=False, label=_('Contact phone'))
    contact_email = forms.EmailField(required=False, label=_('Contact email'))
    invite = forms.BooleanField(
        required=False, initial=True, label=_('Invite to the portal'),
        help_text=_('Sends the welcome email to the contact email: the customer sets a password and completes '
                    'their details, then follows their requests there.'),
    )
    confirmed_new = forms.BooleanField(required=False, label=_('It is a new customer, not one of these'))

    def __init__(self, *args, country=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fixed_country = country
        if country is not None:
            del self.fields['country']
        self.similar = []

    def clean(self):
        cleaned = super().clean()
        name = (cleaned.get('name') or '').strip()
        country = self.fixed_country or cleaned.get('country')
        email = cleaned.get('contact_email')
        if cleaned.get('invite'):
            if not email:
                self.add_error('contact_email', _('Needed to send the portal invitation.'))
            elif get_user_model().objects.filter(username__iexact=email).exists():
                self.add_error('contact_email', _('An account with this email address already exists.'))
        if name and country:
            customers = Customer.objects.filter(country=country, is_internal=False)
            if customers.filter(name__iexact=name).exists():
                raise forms.ValidationError(_(
                    'A customer named %(name)s already exists in this country. Add a branch to it instead.',
                ) % {'name': name})
            if not cleaned.get('confirmed_new'):
                from django.contrib.postgres.search import TrigramSimilarity

                self.similar = list(
                    customers.annotate(similarity=TrigramSimilarity('name', name))
                    .filter(Q(similarity__gt=0.3) | Q(name__icontains=name)).order_by('-similarity')[:5]
                )
                if self.similar:
                    raise forms.ValidationError(_(
                        'Is it one of these customers? If not, tick "It is a new customer" and save again.',
                    ))
        return cleaned

    def save(self):
        data = self.cleaned_data
        customer = Customer.objects.create(
            name=data['name'].strip(), country=self.fixed_country or data['country'], segment=data['segment'],
            contact_name=data['contact_name'], contact_phone=data['contact_phone'],
            contact_email=data['contact_email'] or '',
        )
        site = Site.objects.create(customer=customer, name=data['branch_name'].strip(), address=data['branch_address'])
        return customer, site


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
        fields = [
            'name', 'address', 'contact_name', 'contact_phone', 'contact_email', 'access_notes',
            'warranty_start', 'warranty_until',
        ]
        widgets = {
            'address': forms.Textarea(attrs={'rows': 2}),
            'access_notes': forms.Textarea(attrs={'rows': 2}),
            'warranty_start': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'warranty_until': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }
        help_texts = {
            'warranty_start': _('Normally the installation or handover date. Everything at this branch is free '
                                'while under warranty.'),
            'warranty_until': _('Left empty, it is set to two years after the start.'),
        }

    def __init__(self, *args, customer=None, **kwargs):
        self.customer = customer
        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = self.cleaned_data['name']
        if self.customer and Site.objects.filter(customer=self.customer, name__iexact=name).exists():
            raise forms.ValidationError(_('This customer already has a site with this name.'))
        return name


class CustomerTermsForm(forms.ModelForm):
    """A customer's commercial terms (managers and admins): the discount on
    quotations, and the agreed shipping (per shipment) and service (per
    visit) costs, with how long they last and where they come from."""

    class Meta:
        model = Customer
        fields = ['quotation_discount', 'shipping_cost', 'service_cost', 'terms_valid_until', 'terms_note']
        widgets = {
            'terms_valid_until': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'terms_note': forms.Textarea(attrs={'rows': 2}),
        }
        help_texts = {
            'quotation_discount': _('Off the quotation total. Empty: no discount agreed.'),
            'shipping_cost': _('In the customer\'s country currency. Empty: not agreed.'),
            'service_cost': _('In the customer\'s country currency. Empty: not agreed.'),
        }


class SiteTermsForm(forms.ModelForm):
    """A branch's own commercial terms, where they differ from its
    customer's (managers and admins); each left empty uses the customer's."""

    class Meta:
        model = Site
        fields = ['quotation_discount', 'shipping_cost', 'service_cost']
        help_texts = {
            'quotation_discount': _("Empty: the customer's."),
            'shipping_cost': _("Empty: the customer's."),
            'service_cost': _("Empty: the customer's."),
        }


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
        help_text=_('Paste the coordinates (e.g. 25.0772, 55.1306) or a full Google Maps link. If left blank, '
                    'it will be set by the first technician to tap “Arrived” at this site.'),
    )

    class Meta:
        model = Site
        fields = [
            'name', 'address', 'contact_name', 'contact_phone', 'contact_email', 'access_notes',
            'warranty_start', 'warranty_until',
        ]
        widgets = {
            'address': forms.Textarea(attrs={'rows': 2}),
            'access_notes': forms.Textarea(attrs={'rows': 2}),
            'warranty_start': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'warranty_until': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }
        help_texts = {
            'warranty_start': _('Normally the installation or handover date. Everything at this branch is free '
                                'while under warranty.'),
            'warranty_until': _('Left empty, it is set to two years after the start.'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.latitude is not None:
            self.fields['map_location'].initial = f'{self.instance.latitude}, {self.instance.longitude}'
            if self.instance.location_source == Site.LocationSource.ARRIVAL:
                self.fields['map_location'].help_text = _(
                    'Set by the first technician to arrive. Please verify it and correct it if inaccurate.',
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
                'Coordinates could not be found in the input. Please enter them as “latitude, longitude”, or '
                'paste the full Google Maps link (not a shortened maps.app.goo.gl link).',
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
            raise forms.ValidationError(_('Please upload a .csv file.'))
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


class PortalEmailChangeForm(forms.Form):
    """A customer changing their own sign-in email: the new address and
    their current password. Nothing changes until they open the link sent
    to the new address (accounts.send_email_change_link)."""

    email = forms.EmailField(label=_('New sign-in email'))
    password = forms.CharField(label=_('Your current password'), strip=False, widget=forms.PasswordInput)

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data['email'].strip()
        if email.lower() == self.user.username.lower():
            raise forms.ValidationError(_('This is already your sign-in email.'))
        if get_user_model().objects.filter(username__iexact=email).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError(_('An account with this email address already exists.'))
        return email

    def clean_password(self):
        password = self.cleaned_data['password']
        if not self.user.check_password(password):
            raise forms.ValidationError(_('Your password is not correct.'))
        return password


class PortalBranchRequestForm(forms.Form):
    """A customer asking us to add a branch they've opened. Nothing is
    created from it: it arrives as a request, and an admin adds the branch
    (with its warranty start) from it."""

    name = forms.CharField(label=_('Branch name'), max_length=150)
    address = forms.CharField(label=_('Address'), widget=forms.Textarea(attrs={'rows': 2}))
    contact_name = forms.CharField(label=_('Contact at the branch'), max_length=150, required=False)
    contact_phone = forms.CharField(label=_('Contact phone'), max_length=30, required=False)
    contact_email = forms.EmailField(
        label=_('Contact email'), required=False,
        help_text=_("The branch's own email, if it has one. Empty: your main contact."),
    )
    opening_date = forms.DateField(
        label=_('Opening date (optional)'), required=False,
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
    )
    message = forms.CharField(
        label=_('Anything else we should know'), required=False, widget=forms.Textarea(attrs={'rows': 3}),
    )


class PortalPasswordHelpForm(forms.Form):
    email = forms.EmailField(label=_('Your login email'))


class CustomerSettingsForm(forms.ModelForm):
    """The portal's Settings: the customer's own contact details and
    addresses, and the language the portal shows in (the header's EN/AR
    switch sets the same field). Their login email is changed by staff
    (Customer accounts), not here."""

    class Meta:
        model = Customer
        fields = ['contact_name', 'contact_phone', 'contact_email', 'billing_address', 'shipping_address', 'language']
        labels = {
            'contact_name': _('Contact name'), 'contact_phone': _('Contact phone number'),
            'contact_email': _('Contact email'), 'billing_address': _('Invoice address'),
            'shipping_address': _('Delivery address'), 'language': _('Language'),
        }
        help_texts = {
            'contact_email': _('Where we send updates about your requests.'),
            'billing_address': _('The address invoices should be issued to.'),
            'shipping_address': _('Where spare and replacement parts should be delivered.'),
            'language': _('The language this portal shows in.'),
        }
        widgets = {
            'billing_address': forms.Textarea(attrs={'rows': 2}),
            'shipping_address': forms.Textarea(attrs={'rows': 2}),
        }


class PortalColorForm(forms.ModelForm):
    """The portal's Settings, its own section: the customer's color for
    their portal (buttons and highlights), any color at all."""

    class Meta:
        model = Customer
        fields = ['brand_color']
        labels = {'brand_color': _("Or type your brand's color code")}
        help_texts = {'brand_color': _('From your logo or brand guide, e.g. #0B1F4D.')}
        widgets = {
            'brand_color': forms.TextInput(attrs={
                'maxlength': 7, 'dir': 'ltr', 'spellcheck': 'false', 'autocomplete': 'off', 'placeholder': '#RRGGBB',
                'data-color-code': '',
            }),
        }

    def clean_brand_color(self):
        # "0B1F4D" or "#0B1F4D", as brand guides write it: stored as "#0b1f4d".
        color = self.cleaned_data['brand_color'].strip().lower()
        color = color if color.startswith('#') else f'#{color}'
        if not re.fullmatch(r'#[0-9a-f]{6}', color):
            raise forms.ValidationError(_('Enter a color code like #0B1F4D, or choose your color above.'))
        return color


class CustomerMachineForm(forms.ModelForm):
    """A customer adding one of their own machines from the portal: where it
    is, its brand, model, serial and code, and a photo of its sticker. It
    joins their list waiting for confirmation (Asset.Status.PENDING) — ours
    once a technician records it at a visit or the office confirms it."""

    class Meta:
        model = Asset
        fields = ['site', 'brand', 'model_name', 'serial_no', 'code', 'sticker_photo']
        labels = {
            'model_name': _('Model'), 'serial_no': _('Serial number'), 'code': _('Product code (optional)'),
            'sticker_photo': _('Photo of the sticker'),
        }
        help_texts = {
            'model_name': _('As shown on the machine.'),
            'serial_no': _("As shown on the machine's sticker."),
            'code': _('The product code shown on the sticker, if available.'),
            'sticker_photo': _('A clear photo helps our team confirm the details. Take a photo or upload one.'),
        }
        widgets = {'sticker_photo': forms.ClearableFileInput(attrs={'accept': 'image/*'})}

    def __init__(self, *args, customer, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['site'].queryset = customer.sites.order_by('name')
        sites = list(self.fields['site'].queryset[:2])
        if len(sites) == 1:
            self.fields['site'].initial = sites[0].pk
        self.fields['brand'].queryset = Brand.objects.filter(is_active=True).order_by('name')
        self.fields['serial_no'].required = True
        self.fields['sticker_photo'].validators.append(SafeFileValidator(allowed_extensions=SERIAL_PHOTO_EXTENSIONS))

    def clean_sticker_photo(self):
        photo = self.cleaned_data.get('sticker_photo')
        if photo and photo.size > MAX_TICKET_ATTACHMENT_BYTES:
            raise forms.ValidationError(_('The file exceeds the 25 MB limit. Please select a smaller file or a shorter video.'))
        return photo

    def clean(self):
        cleaned = super().clean()
        serial, site = (cleaned.get('serial_no') or '').strip(), cleaned.get('site')
        if serial and site and Asset.objects.filter(site=site, serial_no__iexact=serial).exists():
            self.add_error('serial_no', _('This machine is already in your list.'))
        cleaned['serial_no'] = serial
        return cleaned
