from django import forms
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from customers.models import Asset, Customer, Site
from people.models import MAX_PHOTO_UPLOAD_BYTES, SKILL_LEVEL_CHOICES, Technician
from reference.models import Brand, Country, Skill, TaskType

from .models import (
    ALLOWED_TICKET_ATTACHMENT_EXTENSIONS, MAX_TICKET_ATTACHMENT_BYTES, CustomerTicket, Task, TaskAssignment,
    TaskAsset, TaskAttachment,
)

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
        'reported_at', 'scheduled_for', 'estimated_hours',
    ]

    class Meta:
        model = Task
        fields = [
            'site', 'task_type', 'brand', 'required_skill', 'min_level',
            'description', 'priority', 'source', 'is_warranty', 'billing_type',
            'reported_at', 'scheduled_for', 'estimated_hours',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'reported_at': forms.DateTimeInput(format=DATETIME_INPUT_FORMAT, attrs={'type': 'datetime-local'}),
            'scheduled_for': forms.DateTimeInput(format=DATETIME_INPUT_FORMAT, attrs={'type': 'datetime-local'}),
        }

    def __init__(self, *args, country, ticket=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['site'].queryset = Site.objects.filter(
            customer__is_active=True, customer__country=country,
        ).select_related('customer')
        self.fields['site'].required = False
        self.fields['task_type'].queryset = TaskType.objects.filter(is_active=True)
        self.fields['brand'].queryset = Brand.objects.filter(is_active=True)
        self.fields['required_skill'].queryset = Skill.objects.filter(is_active=True).select_related('brand')
        self.fields['new_site_customer'].queryset = Customer.objects.filter(is_active=True, country=country)
        # The skill level scale tops out at 4 (see TechnicianSkill.level) —
        # PositiveSmallIntegerField has no upper bound of its own, so without
        # this a nonsense value here is a clean model concern, not just a
        # display one.
        self.fields['min_level'].validators.append(MaxValueValidator(4))
        # No job realistically runs longer than a couple of days unattended;
        # caught here so a typo (20 instead of 2.0) is a clean form error,
        # not a silently absurd estimated finish time.
        self.fields['estimated_hours'].validators.append(MinValueValidator(0))
        self.fields['estimated_hours'].validators.append(MaxValueValidator(48))

        for name in ('reported_at', 'scheduled_for'):
            self.fields[name].input_formats = [DATETIME_INPUT_FORMAT]

        self.fields['priority'].initial = Task.Priority.NORMAL
        self.fields['source'].initial = Task.Source.PHONE
        self.fields['billing_type'].initial = Task.BillingType.CHARGEABLE
        self.fields['reported_at'].initial = timezone.localtime().strftime(DATETIME_INPUT_FORMAT)

        # Coming from a customer ticket — hint the free-text fields with
        # what the customer said, but the site/customer match itself stays
        # a deliberate choice: the supervisor still picks or creates it,
        # since a company name typed by a customer is never a guaranteed
        # match for an existing record.
        if ticket is not None:
            self.fields['source'].initial = Task.Source.PORTAL
            self.fields['description'].initial = ticket.description
            self.fields['reported_at'].initial = timezone.localtime(ticket.submitted_at).strftime(
                DATETIME_INPUT_FORMAT,
            )
            self.fields['new_site_name'].initial = ticket.site_description
            self.fields['new_site_address'].initial = ticket.site_address
            self.fields['new_site_contact_name'].initial = ticket.contact_name
            self.fields['new_site_contact_phone'].initial = ticket.contact_phone

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


class TaskEditForm(forms.ModelForm):
    """Editing an existing task — deliberately narrower than creation.
    `site` isn't here: moving a task to a different site after the fact
    is a different operation (effectively a new task), not an edit.
    Reassigning the lead/helpers stays on the assign screen, which
    already handles that with its own history and reason-tracking.
    """

    class Meta:
        model = Task
        fields = [
            'task_type', 'brand', 'required_skill', 'min_level', 'description', 'priority',
            'source', 'is_warranty', 'billing_type', 'promised_at', 'scheduled_for', 'estimated_hours',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'promised_at': forms.DateTimeInput(format=DATETIME_INPUT_FORMAT, attrs={'type': 'datetime-local'}),
            'scheduled_for': forms.DateTimeInput(format=DATETIME_INPUT_FORMAT, attrs={'type': 'datetime-local'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['task_type'].queryset = TaskType.objects.filter(is_active=True)
        self.fields['brand'].queryset = Brand.objects.filter(is_active=True)
        self.fields['required_skill'].queryset = Skill.objects.filter(is_active=True).select_related('brand')
        self.fields['min_level'].validators.append(MaxValueValidator(4))
        self.fields['estimated_hours'].validators.append(MinValueValidator(0))
        self.fields['estimated_hours'].validators.append(MaxValueValidator(48))

        for name in ('promised_at', 'scheduled_for'):
            self.fields[name].input_formats = [DATETIME_INPUT_FORMAT]
            if self.initial.get(name):
                self.initial[name] = timezone.localtime(self.initial[name]).strftime(DATETIME_INPUT_FORMAT)


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


class SelfRateLevelForm(forms.Form):
    """A technician's own first guess at a skill or conduct-area level —
    shared shape for both, since it's the same 1-4 scale either way.
    """
    level = forms.ChoiceField(choices=[('', '---------')] + SKILL_LEVEL_CHOICES, label=_('Level'))


class ReviewLevelForm(forms.Form):
    """A supervisor's confirmed level — always overwrites whatever was
    there, self-rated or previously supervisor-set.
    """
    level = forms.ChoiceField(choices=[('', '---------')] + SKILL_LEVEL_CHOICES, label=_('Level'))
    note = forms.CharField(required=False, max_length=255, label=_('Note'))


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
        # Hints the phone's picker toward the camera/gallery and video apps
        # instead of a generic file browser — doesn't force the camera, so
        # attaching an existing photo (e.g. one a customer sent) still works.
        widget=forms.FileInput(attrs={'accept': 'image/*,video/*'}),
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_MEDIA_EXTENSIONS)],
    )
    purpose = forms.ChoiceField(choices=TaskAttachment.Purpose.choices, label=_('What is this'))

    def clean_file(self):
        file = self.cleaned_data['file']
        if file.size > MAX_MEDIA_UPLOAD_BYTES:
            raise forms.ValidationError(_('File is too large — the limit is 25 MB.'))
        return file


class PhotoSizeMixin:
    def clean_photo(self):
        photo = self.cleaned_data['photo']
        if photo and photo.size > MAX_PHOTO_UPLOAD_BYTES:
            raise forms.ValidationError(_('Photo is too large — the limit is 5 MB.'))
        return photo


class TechnicianPhotoForm(PhotoSizeMixin, forms.ModelForm):
    """A supervisor/manager setting someone else's photo, from the roster."""

    class Meta:
        model = Technician
        fields = ['photo']
        widgets = {'photo': forms.ClearableFileInput(attrs={'accept': 'image/*'})}


class MyProfileForm(PhotoSizeMixin, forms.ModelForm):
    """A technician editing their own photo, language, phone, and email —
    their own contact details, never anyone else's decision to make. Role,
    country, and employment stay office-side changes.

    Email lives on the linked auth user, not Technician, so it's a plain
    field here rather than a Meta field, kept in sync with request.user
    manually in save().
    """

    email = forms.EmailField(required=False, label=_('email'))

    class Meta:
        model = Technician
        fields = ['photo', 'language', 'phone']
        widgets = {'photo': forms.ClearableFileInput(attrs={'accept': 'image/*'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['email'].initial = self.instance.user.email

    def save(self, commit=True):
        technician = super().save(commit=commit)
        if commit:
            technician.user.email = self.cleaned_data['email']
            technician.user.save(update_fields=['email'])
        return technician


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


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Django's own documented pattern for a multi-file field — a plain
    FileField validates one UploadedFile at a time, so this feeds each
    selected file through that same validation individually and collects
    the results as a list.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', MultipleFileInput(attrs={'multiple': True}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_file_clean(d, initial) for d in data]
        return single_file_clean(data, initial)


class CustomerTicketForm(forms.ModelForm):
    """Public, no login — a customer describing a complaint or request in
    their own words. Self-identified: the company/site names are exactly
    what they typed, not yet matched against anything.

    attachments isn't a CustomerTicket field — it's a list of files
    resolved into individual CustomerTicketAttachment rows by the view,
    once the ticket itself exists to attach them to.
    """

    attachments = MultipleFileField(
        required=False, label=_('Photos and/or short video'),
        help_text=_('showing the issue and the serial number'),
        widget=MultipleFileInput(attrs={'multiple': True, 'accept': 'image/*,video/*'}),
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_TICKET_ATTACHMENT_EXTENSIONS)],
    )

    class Meta:
        model = CustomerTicket
        fields = [
            'country', 'company_name', 'site_description', 'site_address',
            'contact_name', 'contact_phone', 'contact_email',
            'serial_numbers', 'description', 'notes',
        ]
        widgets = {
            'site_address': forms.Textarea(attrs={'rows': 2}),
            'serial_numbers': forms.Textarea(attrs={'rows': 3, 'placeholder': 'SN-12345\nSN-67890'}),
            'description': forms.Textarea(attrs={'rows': 5}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }

    def clean_attachments(self):
        files = self.cleaned_data['attachments']
        for file in files:
            if file.size > MAX_TICKET_ATTACHMENT_BYTES:
                raise forms.ValidationError(_('Each file must be under 25 MB — “%(name)s” is too large.') % {
                    'name': file.name,
                })
        return files

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['country'].queryset = Country.objects.filter(is_active=True)


class DismissTicketForm(forms.Form):
    dismissal_reason = forms.CharField(
        label=_('Reason'), widget=forms.Textarea(attrs={'rows': 3}),
        help_text=_('Why this ticket isn\'t becoming a task — spam, duplicate, not us, etc.'),
    )


class AssignTicketForm(forms.Form):
    """Who's handling this ticket — any active supervisor or manager in
    its own country, not necessarily the person who'll ultimately convert
    or dismiss it. Never a technician: tickets are supervisor-side triage,
    not something that shows up on a technician's own work screens.
    """
    assigned_to = forms.ModelChoiceField(queryset=Technician.objects.none(), label=_('Assign to'))

    def __init__(self, *args, country=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_to'].queryset = Technician.objects.filter(
            is_active=True, country=country,
        ).exclude(role=Technician.Role.TECHNICIAN).order_by('full_name')
