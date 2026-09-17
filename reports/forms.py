from django import forms
from django.core.validators import FileExtensionValidator, RegexValidator
from django.utils.translation import gettext_lazy as _

from .models import CustomerFeedback, WorkReport

SIGNATURE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'gif', 'webp']
MAX_SIGNATURE_UPLOAD_BYTES = 5 * 1024 * 1024


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
    signature = forms.FileField(
        required=False, label=_('Customer signature'),
        widget=forms.FileInput(attrs={'accept': 'image/*'}),
        validators=[FileExtensionValidator(allowed_extensions=SIGNATURE_EXTENSIONS)],
    )

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

    def clean_signature(self):
        signature = self.cleaned_data.get('signature')
        if signature and signature.size > MAX_SIGNATURE_UPLOAD_BYTES:
            raise forms.ValidationError(_('File is too large — the limit is 5 MB.'))
        return signature


class CustomerFeedbackForm(forms.Form):
    rating = forms.ChoiceField(
        choices=CustomerFeedback.RATING_CHOICES,
        widget=forms.RadioSelect, label=_('How would you rate the visit?'),
    )
    comment = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'rows': 3, 'placeholder': _('Anything you\'d like to add?')}),
        label=_('Comments (optional)'),
    )


class PartUsedItemForm(forms.Form):
    # max_length matches PartUsed.part_code/description exactly — this is a
    # plain forms.Form, not a ModelForm, so nothing else catches an
    # oversized value before it reaches PartUsed.objects.create() and fails
    # as an ugly DB error instead of a clean validation message.
    part_code = forms.CharField(required=False, max_length=50, label=_('Part code'))
    description = forms.CharField(required=False, max_length=200, label=_('Description'))
    # PositiveIntegerField has no upper bound of its own; capped here to a
    # figure no real parts count would ever reach, well short of Postgres's
    # integer overflow, which would otherwise surface as the same kind of
    # unhandled DB error.
    quantity = forms.IntegerField(required=False, min_value=1, max_value=99999, label=_('Qty'))
    unit_cost = forms.DecimalField(required=False, min_value=0, max_digits=10, decimal_places=2, label=_('Unit cost'))
    currency_code = forms.CharField(
        required=False, max_length=3, label=_('Currency'),
        validators=[RegexValidator(r'^[A-Z]{3}$', _('Enter a 3-letter currency code, e.g. AED.'))],
    )

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
