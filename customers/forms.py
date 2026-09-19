from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Customer, Site


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
        fields = ['name', 'segment', 'contact_name', 'contact_phone', 'contact_email']


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
