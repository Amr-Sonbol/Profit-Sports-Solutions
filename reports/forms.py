import base64
import binascii

from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.validators import FileExtensionValidator, RegexValidator
from django.utils.translation import gettext_lazy as _

from .models import CustomerFeedback, WorkReport

SIGNATURE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'gif', 'webp']
MAX_SIGNATURE_UPLOAD_BYTES = 5 * 1024 * 1024


def _signature_from_data_url(value):
    """A `data:image/png;base64,...` string from a signature pad, as an
    uploaded PNG file. Raises ValueError if it isn't one.
    """
    header, _sep, encoded = value.partition(',')
    if header != 'data:image/png;base64':
        raise ValueError
    try:
        content = base64.b64decode(encoded, validate=True)
    except binascii.Error:
        raise ValueError
    return SimpleUploadedFile('signature.png', content, content_type='image/png')


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
    # A signature drawn on screen — the web page's signature pad and the
    # mobile app both send it as a PNG data URL. Turned into the same
    # `signature` file an upload would be, so it gets the same checks.
    signature_drawn = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = WorkReport
        fields = ['findings', 'action_taken', 'resolved', 'labour_hours', 'customer_name']
        widgets = {
            'findings': forms.Textarea(attrs={'rows': 3}),
            'action_taken': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, require_signature=False, **kwargs):
        super().__init__(*args, **kwargs)
        # The task type's requires_signature — met by a new upload or one
        # already on file from an earlier filing of this same report.
        self.require_signature = require_signature
        if self.instance and self.instance.pk:
            self.fields['resolved'].initial = str(self.instance.resolved)

    def clean(self):
        cleaned = super().clean()
        if 'signature' in self.errors:
            return cleaned
        signature = cleaned.get('signature')
        drawn = cleaned.get('signature_drawn')
        if not signature and drawn:
            try:
                signature = _signature_from_data_url(drawn)
            except ValueError:
                self.add_error('signature', _('Could not read the signature — please sign again.'))
                return cleaned
        if signature and signature.size > MAX_SIGNATURE_UPLOAD_BYTES:
            self.add_error('signature', _('File is too large — the limit is 5 MB.'))
        elif self.require_signature and not signature and not self.instance.signature_url:
            self.add_error('signature', _('This type of task needs the customer’s signature.'))
        else:
            cleaned['signature'] = signature
        return cleaned


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
