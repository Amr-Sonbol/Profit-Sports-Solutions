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
