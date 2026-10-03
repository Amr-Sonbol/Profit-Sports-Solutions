from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.utils.translation import gettext_lazy as _

from .models import Customer, Site


class CreateLoginForm(forms.Form):
    """Just an email — it becomes both the login username and the address
    the temporary password is emailed to (views.send_customer_login_email),
    one thing for the customer to remember instead of a separate invented
    username. The password itself is system-generated
    (views._generate_temporary_password); the customer sets their own
    real one the first time they sign in.
    """
    email = forms.EmailField(label=_('Email'))

    def clean_email(self):
        email = self.cleaned_data['email']
        if get_user_model().objects.filter(username__iexact=email).exists():
            raise forms.ValidationError(_('A login with that email already exists.'))
        return email


class CustomerFirstLoginForm(forms.Form):
    """Shown once, the first time a customer signs in on a temporary
    password — they set their own password and confirm the contact
    details staff may not have had when the account was created (a bulk
    import, say). Prefilled with whatever's already on file.
    """
    contact_name = forms.CharField(max_length=150, label=_('Your name'))
    contact_phone = forms.CharField(max_length=30, label=_('Phone number'))
    contact_email = forms.EmailField(required=False, label=_('Email'))
    new_password1 = forms.CharField(widget=forms.PasswordInput, label=_('New password'))
    new_password2 = forms.CharField(widget=forms.PasswordInput, label=_('Confirm new password'))

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_new_password2(self):
        password1 = self.cleaned_data.get('new_password1')
        password2 = self.cleaned_data.get('new_password2')
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError(_("The two password fields didn't match."))
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
            'contact_name', 'contact_phone', 'contact_email', 'shipping_address',
        ]
        widgets = {
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
            raise forms.ValidationError(_('This customer already has a site with that name.'))
        return name


class SiteEditForm(forms.ModelForm):
    """Same fields as SiteCreateForm, for a site that already exists —
    contact fields left blank here fall back to the customer's own
    (Site.effective_contact_*), so a shared or single-branch contact
    only needs to live in one place.
    """

    class Meta:
        model = Site
        fields = ['name', 'address', 'contact_name', 'contact_phone', 'contact_email', 'access_notes']
        widgets = {
            'address': forms.Textarea(attrs={'rows': 2}),
            'access_notes': forms.Textarea(attrs={'rows': 2}),
        }

    def clean_name(self):
        name = self.cleaned_data['name']
        if Site.objects.filter(
            customer=self.instance.customer, name__iexact=name,
        ).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_('This customer already has a site with that name.'))
        return name


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
