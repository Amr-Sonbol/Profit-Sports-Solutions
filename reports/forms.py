import base64
import binascii

from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.validators import FileExtensionValidator, RegexValidator
from django.utils.translation import gettext_lazy as _

from reference.models import Part

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
    # The customer wasn't there to sign — files the report anyway, with
    # the reason on record instead of a signature.
    signature_waived = forms.BooleanField(required=False, label=_('Customer not available to sign'))

    class Meta:
        model = WorkReport
        fields = ['findings', 'action_taken', 'resolved', 'labour_hours', 'customer_name', 'signature_waived_reason']
        widgets = {
            'findings': forms.Textarea(attrs={'rows': 3}),
            'action_taken': forms.Textarea(attrs={'rows': 3}),
            'signature_waived_reason': forms.Textarea(attrs={'rows': 2}),
        }
        labels = {'signature_waived_reason': _('Why the customer could not sign')}

    def __init__(self, *args, require_signature=False, helpers=(), **kwargs):
        super().__init__(*args, **kwargs)
        # The task type's requires_signature — met by a new upload or one
        # already on file from an earlier filing of this same report.
        self.require_signature = require_signature
        if self.instance and self.instance.pk:
            self.fields['resolved'].initial = str(self.instance.resolved)
            self.fields['signature_waived'].initial = bool(self.instance.signature_waived_reason)
        # A helper's hours default to the report's own; the lead fills one
        # in only when it differs (someone who left early, say).
        self.helpers = list(helpers)
        for assignment in self.helpers:
            self.fields[f'helper_hours_{assignment.pk}'] = forms.DecimalField(
                required=False, min_value=0, max_digits=5, decimal_places=2,
                initial=assignment.labour_hours, label=assignment.technician.full_name,
                widget=forms.NumberInput(attrs={'step': '0.25', 'placeholder': _('Same as the report')}),
            )

    def helper_fields(self):
        return [self[f'helper_hours_{a.pk}'] for a in self.helpers]

    def helper_hours(self):
        """{helper assignment: hours, or None for "same as the report"}."""
        return {a: self.cleaned_data.get(f'helper_hours_{a.pk}') for a in self.helpers}

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
        waived = cleaned.get('signature_waived')
        if waived and not (cleaned.get('signature_waived_reason') or '').strip():
            self.add_error('signature_waived_reason', _('Say why the customer could not sign.'))
        if not waived:
            cleaned['signature_waived_reason'] = ''
        if signature and signature.size > MAX_SIGNATURE_UPLOAD_BYTES:
            self.add_error('signature', _('File is too large — the limit is 5 MB.'))
        elif self.require_signature and not signature and not self.instance.signature_url and not waived:
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
    part_code = forms.CharField(
        required=False, max_length=50, label=_('Part code'),
        widget=forms.TextInput(attrs={'list': 'parts-catalogue', 'autocomplete': 'off'}),
    )
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

    def clean_part_code(self):
        """Once the catalogue has any active part, the code must be one of
        them — matched case-insensitively and saved as the catalogue spells
        it. While the catalogue is empty, any code goes, as before.
        """
        code = self.cleaned_data.get('part_code', '').strip()
        if not code:
            return code
        catalogue = Part.objects.filter(is_active=True)
        if not catalogue.exists():
            return code
        part = catalogue.filter(code__iexact=code).first()
        if part is None:
            raise forms.ValidationError(
                _('“%(code)s” isn’t in the parts list — pick one from the list, or ask for it to be added.'),
                params={'code': code},
            )
        self.catalogue_part = part
        return part.code

    def clean(self):
        cleaned = super().clean()
        part = getattr(self, 'catalogue_part', None)
        if part and not cleaned.get('description'):
            cleaned['description'] = part.description
        if not any(cleaned.get(f) for f in ('part_code', 'quantity', 'unit_cost')):
            return cleaned
        missing = [f for f in ('part_code', 'quantity', 'unit_cost', 'currency_code') if not cleaned.get(f)]
        if missing:
            raise forms.ValidationError(
                _('Fill in part code, quantity, unit cost and currency, or leave this row blank.'),
            )
        return cleaned
