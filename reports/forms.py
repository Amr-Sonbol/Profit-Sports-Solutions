from django import forms
from django.utils.translation import gettext_lazy as _


class RejectReportForm(forms.Form):
    rejection_reason = forms.CharField(
        label=_('Reason'), widget=forms.Textarea(attrs={'rows': 3}),
        help_text=_('Be specific — missing serial photo, vague fault description, etc. The technician sees this.'),
    )
