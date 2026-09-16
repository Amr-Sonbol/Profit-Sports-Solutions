from django import forms
from django.core.validators import FileExtensionValidator, MaxValueValidator
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from customers.models import Asset, Customer, Site
from people.models import Technician
from reference.models import Brand, Skill, TaskType

from .models import Task, TaskAssignment, TaskAsset, TaskAttachment

DATETIME_INPUT_FORMAT = '%Y-%m-%dT%H:%M'


class TaskCreateForm(forms.ModelForm):
    """Site, brand, task type, and required skill can each be picked from the
    existing list or added on the spot — see the matching new_* fields below.
    Validation only; the actual create-or-reuse resolution happens in the
    view once the whole form is known to be valid, in one atomic block.
    """

    # max_length on each matches the target model field exactly (Site.name,
    # Brand.name, TaskType.code, ...) — these plain forms.Form fields bypass
    # ModelForm's automatic length validation, so without this an oversized
    # value passes form validation clean and then crashes with a DB-level
    # "value too long" error when the view creates the row directly.
    new_site_customer = forms.ModelChoiceField(
        queryset=Customer.objects.none(), required=False, label=_('Customer'),
    )
    new_site_name = forms.CharField(required=False, max_length=150, label=_('Site name'))
    new_site_address = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'rows': 2}), label=_('Address'),
    )
    new_site_contact_name = forms.CharField(required=False, max_length=150, label=_('Contact name'))
    new_site_contact_phone = forms.CharField(required=False, max_length=30, label=_('Contact phone'))
    new_site_access_notes = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'rows': 2}), label=_('Access notes'),
    )

    new_brand_name = forms.CharField(required=False, max_length=100, label=_('Brand name'))
    new_brand_portal_url = forms.URLField(required=False, max_length=200, label=_('Portal URL'))

    new_task_type_code = forms.CharField(required=False, max_length=50, label=_('Code'))
    new_task_type_name = forms.CharField(required=False, max_length=100, label=_('Name'))
    new_task_type_name_ar = forms.CharField(required=False, max_length=100, label=_('Name (Arabic)'))
    new_task_type_category = forms.ChoiceField(
        choices=[('', '---------')] + TaskType.Category.choices, required=False, label=_('Category'),
    )

    new_skill_wanted = forms.BooleanField(required=False, label=_('Add a new skill for this brand'))

    PLAIN_FIELD_NAMES = [
        'min_level', 'description', 'priority', 'source', 'is_warranty', 'billing_type',
        'reported_at', 'scheduled_for',
    ]

    class Meta:
        model = Task
        fields = [
            'site', 'task_type', 'brand', 'required_skill', 'min_level',
            'description', 'priority', 'source', 'is_warranty', 'billing_type',
            'reported_at', 'scheduled_for',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'reported_at': forms.DateTimeInput(format=DATETIME_INPUT_FORMAT, attrs={'type': 'datetime-local'}),
            'scheduled_for': forms.DateTimeInput(format=DATETIME_INPUT_FORMAT, attrs={'type': 'datetime-local'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['site'].queryset = Site.objects.filter(customer__is_active=True).select_related('customer')
        self.fields['site'].required = False
        self.fields['task_type'].queryset = TaskType.objects.filter(is_active=True)
        self.fields['brand'].queryset = Brand.objects.filter(is_active=True)
        self.fields['required_skill'].queryset = Skill.objects.filter(is_active=True).select_related('brand')
        self.fields['new_site_customer'].queryset = Customer.objects.filter(is_active=True)
        # The skill level scale tops out at 4 (see TechnicianSkill.level) —
        # PositiveSmallIntegerField has no upper bound of its own, so without
        # this a nonsense value here is a clean model concern, not just a
        # display one.
        self.fields['min_level'].validators.append(MaxValueValidator(4))

        for name in ('reported_at', 'scheduled_for'):
            self.fields[name].input_formats = [DATETIME_INPUT_FORMAT]

        self.fields['priority'].initial = Task.Priority.NORMAL
        self.fields['source'].initial = Task.Source.PHONE
        self.fields['billing_type'].initial = Task.BillingType.CHARGEABLE
        self.fields['reported_at'].initial = timezone.localtime().strftime(DATETIME_INPUT_FORMAT)

    def plain_fields(self):
        """The fields with no create-or-reuse toggle, for the template's generic loop."""
        return [self[name] for name in self.PLAIN_FIELD_NAMES]

    def clean(self):
        cleaned = super().clean()

        site = cleaned.get('site')
        new_site_name = (cleaned.get('new_site_name') or '').strip()
        new_site_customer = cleaned.get('new_site_customer')
        if site and (new_site_name or new_site_customer):
            self.add_error(None, _('Choose an existing site or add a new one below, not both.'))
        elif not site and not (new_site_name and new_site_customer):
            self.add_error('site', _('Choose a site, or add a new one below.'))
        elif not site and Site.objects.filter(customer=new_site_customer, name__iexact=new_site_name).exists():
            self.add_error('new_site_name', _('This customer already has a site with that name.'))

        brand = cleaned.get('brand')
        new_brand_name = (cleaned.get('new_brand_name') or '').strip()
        if brand and new_brand_name:
            self.add_error(None, _('Choose an existing brand or add a new one below, not both.'))
        elif new_brand_name and Brand.objects.filter(name__iexact=new_brand_name).exists():
            self.add_error(
                'new_brand_name', _('A brand with that name already exists — pick it from the list instead.'),
            )

        task_type = cleaned.get('task_type')
        new_task_type_fields = (
            cleaned.get('new_task_type_code'), cleaned.get('new_task_type_name'),
            cleaned.get('new_task_type_name_ar'), cleaned.get('new_task_type_category'),
        )
        if task_type and any(new_task_type_fields):
            self.add_error(None, _('Choose an existing task type or add a new one below, not both.'))
        elif any(new_task_type_fields) and not all(new_task_type_fields):
            self.add_error(
                None, _('Fill in code, name, Arabic name, and category for the new task type, or leave them blank.'),
            )
        elif all(new_task_type_fields):
            if TaskType.objects.filter(code__iexact=cleaned['new_task_type_code'].strip()).exists():
                self.add_error('new_task_type_code', _('A task type with that code already exists.'))
            if TaskType.objects.filter(name__iexact=cleaned['new_task_type_name'].strip()).exists():
                self.add_error(
                    'new_task_type_name',
                    _('A task type with that name already exists — pick it from the list instead.'),
                )

        required_skill = cleaned.get('required_skill')
        new_skill_wanted = cleaned.get('new_skill_wanted')
        if required_skill and new_skill_wanted:
            self.add_error(None, _('Choose an existing required skill or add a new one, not both.'))
        elif new_skill_wanted and not (brand or new_brand_name):
            self.add_error('new_skill_wanted', _('Pick or add a brand first — a skill always belongs to one.'))

        return cleaned


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


class MarkUnavailableForm(forms.Form):
    reason = forms.ChoiceField(
        choices=[('', '---------')] + Technician.UnavailableReason.choices, label=_('Reason'),
    )


ALLOWED_MEDIA_EXTENSIONS = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'heic', 'mp4', 'mov', 'webm']
MAX_MEDIA_UPLOAD_BYTES = 25 * 1024 * 1024


class TaskAttachmentUploadForm(forms.Form):
    """Extensions are allow-listed (no .svg/.html/...), not just content-type

    checked, because attachments are served back same-origin as raw files
    (see task_detail.html) and the server infers content-type from the
    extension — accepting anything else would let a stored file execute as
    script in the app's own origin when a supervisor opens the link.
    """

    file = forms.FileField(
        label=_('Photo or video'),
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_MEDIA_EXTENSIONS)],
    )
    purpose = forms.ChoiceField(choices=TaskAttachment.Purpose.choices, label=_('What is this'))

    def clean_file(self):
        file = self.cleaned_data['file']
        if file.size > MAX_MEDIA_UPLOAD_BYTES:
            raise forms.ValidationError(_('File is too large — the limit is 25 MB.'))
        return file


class BlockTaskForm(forms.Form):
    note = forms.CharField(
        label=_('What happened'), widget=forms.Textarea(attrs={'rows': 2}),
        help_text=_('e.g. gym closed, no key, customer absent'),
    )


class ExistingAssetOutcomeForm(forms.Form):
    """A machine already at this site that this visit actually covered.

    Blank optional rows, like NewAssetForm below — the technician picks only
    the machine(s) this visit was about, not every machine the site owns.
    """

    asset = forms.ModelChoiceField(
        queryset=Asset.objects.none(), required=False, label=_('Machine'),
    )
    outcome = forms.ChoiceField(
        choices=[('', '---------')] + TaskAsset.Outcome.choices, required=False, label=_('Outcome'),
    )

    def __init__(self, *args, site=None, **kwargs):
        super().__init__(*args, **kwargs)
        if site is not None:
            self.fields['asset'].queryset = Asset.objects.filter(site=site).select_related('brand')

    def clean(self):
        cleaned = super().clean()
        if not any(cleaned.get(f) for f in ('asset', 'outcome')):
            return cleaned
        if not cleaned.get('asset') or not cleaned.get('outcome'):
            raise forms.ValidationError(
                _('Choose the machine and an outcome, or leave this row blank.'),
            )
        return cleaned


class NewAssetForm(forms.Form):
    """A machine not yet in the system — created here, per the doc's 'assets get created' step."""

    brand = forms.ModelChoiceField(
        queryset=Brand.objects.filter(is_active=True), required=False, label=_('Brand'),
    )
    model_name = forms.CharField(
        required=False, max_length=150, label=_('Model'), help_text=_('free text from the plate'),
    )
    serial_no = forms.CharField(required=False, max_length=100, label=_('Serial number'))
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
