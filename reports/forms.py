from django import forms
from django.utils.translation import gettext_lazy as _

from .models import WorkReport


class RejectReportForm(forms.Form):
    rejection_reason = forms.CharField(
        label=_('Reason'), widget=forms.Textarea(attrs={'rows': 3}),
        help_text=_('Be specific — missing serial photo, vague fault description, etc. The technician sees this.'),
    )


class WorkReportForm(forms.ModelForm):
    resolved = forms.TypedChoiceField(
        choices=[('True', _('Yes')), ('False', _('No'))], coerce=lambda value: value == 'True',
        widget=forms.RadioSelect, label=_('Resolved'),
    )
    signature = forms.FileField(required=False, label=_('Customer signature'))

    class Meta:
        model = WorkReport
        fields = ['findings', 'action_taken', 'resolved', 'labour_hours', 'customer_name']
        widgets = {
            'findings': forms.Textarea(attrs={'rows': 3}),
            'action_taken': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['resolved'].initial = str(self.instance.resolved)


class PartUsedItemForm(forms.Form):
    part_code = forms.CharField(required=False, label=_('Part code'))
    description = forms.CharField(required=False, label=_('Description'))
    quantity = forms.IntegerField(required=False, min_value=1, label=_('Qty'))
    unit_cost = forms.DecimalField(required=False, min_value=0, max_digits=10, decimal_places=2, label=_('Unit cost'))
    currency_code = forms.CharField(required=False, max_length=3, label=_('Currency'))

    def clean(self):
        cleaned = super().clean()
        if not any(cleaned.get(f) for f in ('part_code', 'quantity', 'unit_cost')):
            return cleaned
        missing = [f for f in ('part_code', 'quantity', 'unit_cost', 'currency_code') if not cleaned.get(f)]
        if missing:
            raise forms.ValidationError(
                _('Fill in part code, quantity, unit cost and currency, or leave this row blank.'),
            )
        return cleaned
