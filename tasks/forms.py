from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from customers.models import Site
from people.models import Technician
from reference.models import Brand, Skill, TaskType

from .models import Task, TaskAssignment, TaskAsset, TaskAttachment

DATETIME_INPUT_FORMAT = '%Y-%m-%dT%H:%M'


class TaskCreateForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = [
            'site', 'task_type', 'brand', 'required_skill', 'min_level',
            'description', 'priority', 'source', 'is_warranty', 'billing_type',
            'reported_at', 'promised_at', 'scheduled_for',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'reported_at': forms.DateTimeInput(format=DATETIME_INPUT_FORMAT, attrs={'type': 'datetime-local'}),
            'promised_at': forms.DateTimeInput(format=DATETIME_INPUT_FORMAT, attrs={'type': 'datetime-local'}),
            'scheduled_for': forms.DateTimeInput(format=DATETIME_INPUT_FORMAT, attrs={'type': 'datetime-local'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['site'].queryset = Site.objects.filter(customer__is_active=True).select_related('customer')
        self.fields['task_type'].queryset = TaskType.objects.filter(is_active=True)
        self.fields['brand'].queryset = Brand.objects.filter(is_active=True)
        self.fields['required_skill'].queryset = Skill.objects.filter(is_active=True).select_related('brand')

        for name in ('reported_at', 'promised_at', 'scheduled_for'):
            self.fields[name].input_formats = [DATETIME_INPUT_FORMAT]

        self.fields['priority'].initial = Task.Priority.NORMAL
        self.fields['source'].initial = Task.Source.PHONE
        self.fields['billing_type'].initial = Task.BillingType.CHARGEABLE
        self.fields['reported_at'].initial = timezone.localtime().strftime(DATETIME_INPUT_FORMAT)


class SetLeadForm(forms.Form):
    technician = forms.ModelChoiceField(queryset=Technician.objects.none(), label=_('Technician'))
    end_reason = forms.ChoiceField(
        choices=[('', '---------')] + TaskAssignment.EndReason.choices, required=False,
        label=_('Reason for replacing the current lead'),
    )

    def __init__(self, *args, technicians, requires_reason, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['technician'].queryset = technicians
        self.requires_reason = requires_reason
        if not requires_reason:
            del self.fields['end_reason']

    def clean_end_reason(self):
        end_reason = self.cleaned_data['end_reason']
        if self.requires_reason and not end_reason:
            raise forms.ValidationError(_('Choose a reason for replacing the current lead.'))
        return end_reason


class AddHelperForm(forms.Form):
    technician = forms.ModelChoiceField(queryset=Technician.objects.none(), label=_('Technician'))

    def __init__(self, *args, technicians, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['technician'].queryset = technicians


class RemoveAssignmentForm(forms.Form):
    end_reason = forms.ChoiceField(
        choices=[('', '---------')] + TaskAssignment.EndReason.choices, label=_('Reason'),
    )


class TaskAttachmentUploadForm(forms.Form):
    file = forms.FileField(label=_('Photo or video'))
    purpose = forms.ChoiceField(choices=TaskAttachment.Purpose.choices, label=_('What is this'))


class BlockTaskForm(forms.Form):
    note = forms.CharField(
        label=_('What happened'), widget=forms.Textarea(attrs={'rows': 2}),
        help_text=_('e.g. gym closed, no key, customer absent'),
    )


class ExistingAssetOutcomeForm(forms.Form):
    """One row per asset already known at the site — tick it if this visit covered it."""

    asset_id = forms.IntegerField(widget=forms.HiddenInput())
    include = forms.BooleanField(required=False, label='')
    outcome = forms.ChoiceField(
        choices=[('', '---------')] + TaskAsset.Outcome.choices, required=False, label=_('Outcome'),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('include') and not cleaned.get('outcome'):
            raise forms.ValidationError(_('Choose an outcome for this machine.'))
        return cleaned


class NewAssetForm(forms.Form):
    """A machine not yet in the system — created here, per the doc's 'assets get created' step."""

    brand = forms.ModelChoiceField(
        queryset=Brand.objects.filter(is_active=True), required=False, label=_('Brand'),
    )
    model_name = forms.CharField(required=False, label=_('Model'), help_text=_('free text from the plate'))
    serial_no = forms.CharField(required=False, label=_('Serial number'))
    outcome = forms.ChoiceField(
        choices=[('', '---------')] + TaskAsset.Outcome.choices, required=False, label=_('Outcome'),
    )

    def clean(self):
        cleaned = super().clean()
        if not any(cleaned.get(f) for f in ('brand', 'model_name', 'serial_no', 'outcome')):
            return cleaned
        if not cleaned.get('brand') or not cleaned.get('model_name') or not cleaned.get('outcome'):
            raise forms.ValidationError(
                _('Enter at least a brand, model, and outcome for each new machine, or leave the row blank.'),
            )
        return cleaned
