from django import forms
from django.contrib.auth import forms as auth_forms
from django.contrib.auth import get_user_model, password_validation
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from customers.models import Asset, Customer, Site
from people.models import (
    ALLOWED_SKILL_EVIDENCE_EXTENSIONS, MAX_PHOTO_UPLOAD_BYTES, MAX_SKILL_EVIDENCE_UPLOAD_BYTES,
    SKILL_LEVEL_CHOICES, NotificationSettings, RolePermission, Technician, TechnicianTrip,
)
from reference.models import Brand, ConductArea, Country, Part, Skill, TaskType
from reports.phrases import Action, Finding

from .models import (
    ALLOWED_TICKET_ATTACHMENT_EXTENSIONS, MAX_TASK_DOCUMENT_BYTES, MAX_TICKET_ATTACHMENT_BYTES,
    MAX_TICKET_ATTACHMENT_COUNT, MAX_TICKET_MACHINES, SERIAL_PHOTO_EXTENSIONS, CustomerTicket, Task, TaskAssignment,
    TaskAsset,
    TaskAttachment, TaskEvent, TicketEscalation, TicketMachine,
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

    PLAIN_FIELD_NAMES = [
        'required_skill', 'min_level', 'description', 'priority', 'source', 'is_warranty', 'billing_type',
        'reported_at', 'scheduled_for', 'estimated_hours', 'responsible_supervisor',
    ]

    class Meta:
        model = Task
        fields = [
            'site', 'task_type', 'brand', 'required_skill', 'min_level',
            'description', 'priority', 'source', 'is_warranty', 'billing_type',
            'reported_at', 'scheduled_for', 'estimated_hours', 'responsible_supervisor',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'reported_at': forms.DateTimeInput(format=DATETIME_INPUT_FORMAT, attrs={'type': 'datetime-local'}),
            'scheduled_for': forms.DateTimeInput(format=DATETIME_INPUT_FORMAT, attrs={'type': 'datetime-local'}),
        }

    def __init__(self, *args, country, ticket=None, is_admin=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_admin = is_admin
        self.fields['site'].queryset = Site.objects.filter(
            customer__is_active=True, customer__country=country,
        ).select_related('customer')
        self.fields['site'].required = False
        self.fields['task_type'].queryset = TaskType.objects.filter(is_active=True)
        self.fields['brand'].queryset = Brand.objects.filter(is_active=True)
        self.fields['required_skill'].queryset = Skill.objects.filter(is_active=True)
        if is_admin:
            self.fields['new_site_customer'].queryset = Customer.objects.filter(is_active=True, country=country)
        else:
            # Creating a site is admin-only (customers.views.customer_detail
            # is the same rule) — without this, "+ Add new site" here would
            # be a second, unrestricted door to the same thing.
            for field_name in [
                'new_site_customer', 'new_site_name', 'new_site_address',
                'new_site_contact_name', 'new_site_contact_phone', 'new_site_access_notes',
            ]:
                del self.fields[field_name]
        # Any active supervisor or manager in the country, same pool as
        # AssignTicketForm's assigned_to — never a technician, and picking
        # one here is entirely optional (see Task.responsible_supervisor).
        self.fields['responsible_supervisor'].queryset = Technician.objects.filter(
            is_active=True, country=country,
        ).exclude(role=Technician.Role.TECHNICIAN).order_by('full_name')
        self.fields['responsible_supervisor'].required = False
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
        # what the customer said.
        if ticket is not None:
            # Same values as Task.Source — a ticket staff opened from a
            # phone call keeps "Phone" on the task.
            self.fields['source'].initial = ticket.source
            if ticket.kind != CustomerTicket.Kind.CUSTOMER:
                # A stock request or vendor follow-up came from inside the
                # company, whatever the ticket's own default says.
                self.fields['source'].initial = Task.Source.INTERNAL
                self.fields['description'].initial = f'{ticket.subject}\n\n{ticket.description}'.strip()
            else:
                self.fields['description'].initial = ticket.description
            self.fields['reported_at'].initial = timezone.localtime(ticket.submitted_at).strftime(
                DATETIME_INPUT_FORMAT,
            )

            if ticket.site_id:
                # A logged-in customer picked one of their own registered
                # sites at submission — that's authoritative, not a guess,
                # so it carries straight through with no re-matching and
                # no chance to pick something else in between. Only an
                # admin can override it.
                self.fields['site'].initial = ticket.site_id
                if not is_admin:
                    self.fields['site'].disabled = True
            else:
                # No registered site to go on (an anonymous public
                # submission) — best-effort match by name, same
                # case-insensitive match the CSV import uses, so the task
                # still lands on a real record instead of a look-alike
                # duplicate where possible. Always just a guess here, so
                # always editable.
                matched_customer = Customer.objects.filter(
                    country=country, is_active=True, name__iexact=ticket.company_name.strip(),
                ).first()
                matched_site = None
                if matched_customer:
                    matched_site = Site.objects.filter(
                        customer=matched_customer, name__iexact=ticket.site_description.strip(),
                    ).first()

                if matched_site:
                    self.fields['site'].initial = matched_site.pk
                elif is_admin:
                    self.fields['new_site_name'].initial = ticket.site_description
                    self.fields['new_site_address'].initial = ticket.site_address
                    self.fields['new_site_contact_name'].initial = ticket.contact_name
                    self.fields['new_site_contact_phone'].initial = ticket.contact_phone
                    if matched_customer:
                        self.fields['new_site_customer'].initial = matched_customer.pk

    def plain_fields(self):
        """The fields with no create-or-reuse toggle, for the template's generic loop."""
        return [self[name] for name in self.PLAIN_FIELD_NAMES]

    def clean(self):
        cleaned = super().clean()

        site = cleaned.get('site')
        new_site_name = (cleaned.get('new_site_name') or '').strip()
        new_site_customer = cleaned.get('new_site_customer')
        if site and (new_site_name or new_site_customer):
            self.add_error(None, _('Select an existing site or add a new one below, not both.'))
        elif not site and not (new_site_name and new_site_customer):
            if self.is_admin:
                self.add_error('site', _('Select a site, or add a new one below.'))
            else:
                self.add_error('site', _('Select a site. An administrator can add a new site if it is not listed.'))
        elif not site and Site.objects.filter(customer=new_site_customer, name__iexact=new_site_name).exists():
            self.add_error('new_site_name', _('This customer already has a site with this name.'))

        brand = cleaned.get('brand')
        new_brand_name = (cleaned.get('new_brand_name') or '').strip()
        if brand and new_brand_name:
            self.add_error(None, _('Select an existing brand or add a new one below, not both.'))
        elif new_brand_name and Brand.objects.filter(name__iexact=new_brand_name).exists():
            self.add_error(
                'new_brand_name', _('A brand with this name already exists. Please select it from the list.'),
            )

        task_type = cleaned.get('task_type')
        new_task_type_fields = (
            cleaned.get('new_task_type_code'), cleaned.get('new_task_type_name'),
            cleaned.get('new_task_type_name_ar'), cleaned.get('new_task_type_category'),
        )
        if task_type and any(new_task_type_fields):
            self.add_error(None, _('Select an existing task type or add a new one below, not both.'))
        elif any(new_task_type_fields) and not all(new_task_type_fields):
            self.add_error(
                None, _('Please complete the code, name, Arabic name and category for the new task type, or '
                        'leave them blank.'),
            )
        elif all(new_task_type_fields):
            if TaskType.objects.filter(code__iexact=cleaned['new_task_type_code'].strip()).exists():
                self.add_error('new_task_type_code', _('A task type with this code already exists.'))
            if TaskType.objects.filter(name__iexact=cleaned['new_task_type_name'].strip()).exists():
                self.add_error(
                    'new_task_type_name',
                    _('A task type with this name already exists. Please select it from the list.'),
                )

        return cleaned


# Once the report is filed, these are the record of the job — what the
# customer is billed, what the hours are measured against, who answers for
# it. A supervisor sets them while the task is open; after that only the
# manager tier changes them. (Product lines follow the same idea, see
# PRODUCTS_LOCKED_STATUSES in views.py.)
REPORT_FILED_STATUSES = {
    Task.Status.PENDING_SUPERVISOR_REVIEW, Task.Status.COMPLETED, Task.Status.CLOSED, Task.Status.CANCELLED,
}
LOCKED_AFTER_REPORT_FIELDS = ['estimated_hours', 'billing_type', 'is_warranty', 'responsible_supervisor']


class TaskEditForm(forms.ModelForm):
    """Editing an existing task — deliberately narrower than creation.
    `site` isn't here: moving a task to a different site after the fact
    is a different operation (effectively a new task), not an edit.
    Reassigning the lead/helpers stays on the assign screen, which
    already handles that with its own history and reason-tracking.

    `responsible_supervisor` is here, though: unlike the lead, it has no
    history to track, and this is also how an unowned task gets claimed
    (or a manager reassigns one) — see _require_task_owner in views.py.

    Scheduling carries two extra manager-only controls on top of the
    plain `scheduled_for` datetime — `schedule_mode` and
    `scheduled_date_only` — see Task.schedule_time_locked for what
    day-only vs. locked scheduling means. A supervisor never sees
    either, and loses `scheduled_for` itself too once the task is
    locked or has a manager's day pinned with no time yet — from that
    point the schedule only moves through set_schedule_time /
    request_schedule_change (tasks/views.py), not a plain edit.
    """

    class ScheduleMode(models.TextChoices):
        FULL = 'full', _('Day and exact time')
        DAY_ONLY = 'day_only', _('Day only — the supervisor will set the exact time')

    schedule_mode = forms.ChoiceField(
        choices=ScheduleMode.choices, required=False, widget=forms.RadioSelect,
        label=_('Scheduling'),
    )
    scheduled_date_only = forms.DateField(
        required=False, widget=forms.DateInput(attrs={'type': 'date'}), label=_('Scheduled date'),
        help_text=_('used instead of the time below when scheduling by day only'),
    )

    class Meta:
        model = Task
        fields = [
            'task_type', 'brand', 'required_skill', 'min_level', 'description', 'reported_serial_numbers',
            'priority', 'source', 'is_warranty', 'billing_type', 'promised_at', 'scheduled_for', 'estimated_hours',
            'responsible_supervisor', 'pak_reference_number', 'shipping_company', 'shipping_tracking_number',
            'quotation', 'factory_offer', 'invoice', 'invoice_visible_to_supervisor', 'delivery_note',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'reported_serial_numbers': forms.Textarea(attrs={'rows': 3}),
            'promised_at': forms.DateTimeInput(format=DATETIME_INPUT_FORMAT, attrs={'type': 'datetime-local'}),
            'scheduled_for': forms.DateTimeInput(format=DATETIME_INPUT_FORMAT, attrs={'type': 'datetime-local'}),
            'quotation': forms.ClearableFileInput(attrs={'accept': 'application/pdf,image/*'}),
            'factory_offer': forms.ClearableFileInput(attrs={'accept': 'application/pdf,image/*'}),
            'invoice': forms.ClearableFileInput(attrs={'accept': 'application/pdf,image/*'}),
            'delivery_note': forms.ClearableFileInput(attrs={'accept': 'application/pdf,image/*'}),
        }

    def __init__(self, *args, country, is_manager, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_manager = is_manager
        self.fields['task_type'].queryset = TaskType.objects.filter(is_active=True)
        self.fields['brand'].queryset = Brand.objects.filter(is_active=True)
        self.fields['required_skill'].queryset = Skill.objects.filter(is_active=True)
        self.fields['min_level'].validators.append(MaxValueValidator(4))
        self.fields['estimated_hours'].validators.append(MinValueValidator(0))
        self.fields['estimated_hours'].validators.append(MaxValueValidator(48))
        self.fields['responsible_supervisor'].queryset = Technician.objects.filter(
            is_active=True, country=country,
        ).exclude(role=Technician.Role.TECHNICIAN).order_by('full_name')
        self.fields['responsible_supervisor'].required = False

        self.fields['promised_at'].input_formats = [DATETIME_INPUT_FORMAT]
        if self.initial.get('promised_at'):
            self.initial['promised_at'] = timezone.localtime(self.initial['promised_at']).strftime(
                DATETIME_INPUT_FORMAT,
            )

        instance = self.instance
        locked = bool(instance.pk and instance.schedule_time_locked)
        day_only_pending = bool(instance.pk and instance.scheduled_date and not instance.scheduled_for)

        if is_manager:
            self.fields['scheduled_for'].required = False
            self.initial['schedule_mode'] = (
                self.ScheduleMode.DAY_ONLY if day_only_pending
                else self.ScheduleMode.FULL if instance.scheduled_for else ''
            )
            if instance.scheduled_date:
                self.initial['scheduled_date_only'] = instance.scheduled_date
            self.fields['scheduled_for'].input_formats = [DATETIME_INPUT_FORMAT]
            if self.initial.get('scheduled_for'):
                self.initial['scheduled_for'] = timezone.localtime(self.initial['scheduled_for']).strftime(
                    DATETIME_INPUT_FORMAT,
                )
            # schedule_mode/scheduled_date_only are declared fields, not
            # Meta fields, so Django appends them after every Meta field —
            # all the way past the shipment/document fields, nowhere near
            # scheduled_for itself. Move them to sit right next to it.
            self.order_fields([
                'task_type', 'brand', 'required_skill', 'min_level', 'description',
                'reported_serial_numbers', 'priority',
                'source', 'is_warranty', 'billing_type', 'promised_at',
                'schedule_mode', 'scheduled_for', 'scheduled_date_only',
                'estimated_hours', 'responsible_supervisor',
                'pak_reference_number', 'shipping_company', 'shipping_tracking_number',
                'quotation', 'factory_offer', 'invoice', 'invoice_visible_to_supervisor', 'delivery_note',
            ])
        else:
            # A supervisor only ever sees the plain scheduled_for field
            # in the one case they can still freely edit it — no lock in
            # effect and no manager day-only date pinned yet. Otherwise
            # it's dropped entirely; task_detail's own small actions
            # (set_schedule_time / request_schedule_change) take over.
            del self.fields['schedule_mode']
            del self.fields['scheduled_date_only']
            if locked or day_only_pending:
                del self.fields['scheduled_for']
            else:
                self.fields['scheduled_for'].input_formats = [DATETIME_INPUT_FORMAT]
                if self.initial.get('scheduled_for'):
                    self.initial['scheduled_for'] = timezone.localtime(self.initial['scheduled_for']).strftime(
                        DATETIME_INPUT_FORMAT,
                    )
            # Quotation/factory offer/invoice/delivery note are manager-tier
            # documents — a supervisor never sees or uploads them, same as
            # task_detail's own Documents section. Whether the invoice
            # itself is also shown to the supervisor is a manager-only
            # toggle too — a supervisor can't grant themselves that.
            for field_name in [
                'quotation', 'factory_offer', 'invoice', 'invoice_visible_to_supervisor', 'delivery_note',
            ]:
                del self.fields[field_name]

            # Disabled, not removed: still shown, and Django ignores any
            # posted value for a disabled field, keeping the saved one.
            if instance.pk and instance.status in REPORT_FILED_STATUSES:
                for field_name in LOCKED_AFTER_REPORT_FIELDS:
                    self.fields[field_name].disabled = True
                    self.fields[field_name].help_text = _(
                        'Only a manager or administrator can change this once the report has been submitted.',
                    )
            # The promised date is what on-time arrival is measured
            # against, so it stops moving once work has actually started.
            if instance.pk and instance.events.filter(event_type=TaskEvent.EventType.STARTED).exists():
                self.fields['promised_at'].disabled = True
                self.fields['promised_at'].help_text = _(
                    'Only a manager or administrator can change this once work has started.',
                )

    def clean(self):
        cleaned = super().clean()
        if self.is_manager:
            mode = cleaned.get('schedule_mode')
            date_only = cleaned.get('scheduled_date_only')
            full_datetime = cleaned.get('scheduled_for')
            # Infer the mode from whichever field actually has a value if
            # the radio itself wasn't touched — the two fields sit far
            # apart in the form (scheduled_for near the top of the task's
            # own fields, the radio down with the rest of scheduling), so
            # requiring the radio as well as the field it implies was an
            # easy-to-miss trap: fill in "Scheduled for" and save, and the
            # only feedback was an error on a completely different field.
            if not mode:
                mode = self.ScheduleMode.FULL if full_datetime else self.ScheduleMode.DAY_ONLY if date_only else ''
                cleaned['schedule_mode'] = mode
            if mode == self.ScheduleMode.DAY_ONLY and not date_only:
                self.add_error('scheduled_date_only', _('Enter a date.'))
            elif mode == self.ScheduleMode.FULL and not full_datetime:
                self.add_error('scheduled_for', _('Enter the exact date and time.'))
        return cleaned

    def _clean_document(self, field_name):
        document = self.cleaned_data[field_name]
        if document and document.size > MAX_TASK_DOCUMENT_BYTES:
            raise forms.ValidationError(_('The file is too large. The maximum size is 10 MB.'))
        return document

    def clean_quotation(self):
        return self._clean_document('quotation')

    def clean_factory_offer(self):
        return self._clean_document('factory_offer')

    def clean_invoice(self):
        return self._clean_document('invoice')

    def clean_delivery_note(self):
        return self._clean_document('delivery_note')

    def save(self, commit=True):
        task = super().save(commit=False)
        now = timezone.now()
        for field_name in ('quotation', 'factory_offer', 'invoice', 'delivery_note'):
            if field_name in self.changed_data:
                setattr(task, f'{field_name}_uploaded_at', now if getattr(task, field_name) else None)

        if self.is_manager:
            mode = self.cleaned_data.get('schedule_mode')
            if mode == self.ScheduleMode.DAY_ONLY:
                task.scheduled_date = self.cleaned_data['scheduled_date_only']
                task.scheduled_for = None
                task.schedule_time_locked = False
            elif mode == self.ScheduleMode.FULL:
                task.scheduled_date = timezone.localtime(task.scheduled_for).date()
                task.schedule_time_locked = True
            else:
                task.scheduled_date = None
                task.scheduled_for = None
                task.schedule_time_locked = False
        elif 'scheduled_for' in self.fields:
            # The one case a supervisor can still freely edit it —
            # keep scheduled_date in sync so day-only logic elsewhere
            # always has a consistent picture of what's pinned.
            task.scheduled_date = timezone.localtime(task.scheduled_for).date() if task.scheduled_for else None

        if commit:
            task.save()
        return task


class SetLeadForm(forms.Form):
    technician = forms.ModelChoiceField(queryset=Technician.objects.none(), label=_('Technician'))
    end_reason = forms.ChoiceField(
        choices=[('', '---------')] + TaskAssignment.EndReason.choices, required=False,
        label=_('Reason for replacing the current lead'),
    )
    # Only when replacing a lead on a job already underway (ask_hours).
    hours_worked = forms.DecimalField(
        required=False, min_value=0, max_digits=5, decimal_places=2,
        label=_('Hours already worked by the current lead'),
        help_text=_('Counted toward their hours, as the report will be submitted by the new lead. Leave blank '
                    'if unknown.'),
    )

    def __init__(self, *args, technicians, requires_reason, ask_hours=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['technician'].queryset = technicians
        self.requires_reason = requires_reason
        if not requires_reason:
            del self.fields['end_reason']
        if not ask_hours:
            del self.fields['hours_worked']

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


class CreateTechnicianLoginForm(forms.Form):
    """Just an email — it becomes both the login username and the address
    the one-time setup link goes to (views.send_staff_setup_link), where
    the technician sets their own password. No password is ever emailed.
    """
    email = forms.EmailField(label=_('Email'))

    def clean_email(self):
        email = self.cleaned_data['email']
        if get_user_model().objects.filter(username__iexact=email).exists():
            raise forms.ValidationError(_('An account with this email address already exists.'))
        return email


class DeactivateTechnicianForm(forms.Form):
    """Permanent, unlike MarkUnavailableForm above — resigned, terminated,
    etc. Free text rather than fixed choices: the reasons here vary too
    much to usefully bucket, and this is a one-off note, not reported on.
    """
    reason = forms.CharField(label=_('Reason'), widget=forms.Textarea(attrs={'rows': 2}))


# One plain line for AUTH_PASSWORD_VALIDATORS, instead of Django's own
# four-bullet list, on every screen where someone picks a password.
PASSWORD_RULES = _('At least 8 characters, not entirely numeric, and not a common password or one similar to '
                   'your name.')


def new_password_fields():
    """The (new_password1, new_password2) pair every set-a-password form uses."""
    return (
        forms.CharField(
            label=_('New password'), help_text=PASSWORD_RULES, strip=False,
            widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        ),
        forms.CharField(
            label=_('Confirm new password'), strip=False,
            widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        ),
    )


class ChangePasswordForm(auth_forms.PasswordChangeForm):
    """My profile: change your own password, with plain labels."""
    new_password1, new_password2 = new_password_fields()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['old_password'].label = _('Current password')


class NewPasswordForm(auth_forms.SetPasswordForm):
    """Set a password without the current one: a manager's reset, or a
    customer's emailed link."""
    new_password1, new_password2 = new_password_fields()


class TechnicianFirstLoginForm(forms.Form):
    """Shown once, the first time a technician signs in on a temporary
    password — just the password itself; everything else about their own
    record (photo, language) is already editable any time from My profile,
    not a one-time gate the way a customer's contact details are.
    """
    new_password1, new_password2 = new_password_fields()

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_new_password2(self):
        password1 = self.cleaned_data.get('new_password1')
        password2 = self.cleaned_data.get('new_password2')
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError(_('The two password fields do not match.'))
        if password1:
            password_validation.validate_password(password1, self.user)
        return password2


# The four sentences every level is judged by, for a skill or a conduct
# area alike (docs/database_design_v2.md, "How a level gets set"): the
# full sentence on My skills, the short form in the level choices.
SKILL_LEVEL_RUBRIC = {
    1: _('Has never done this repair alone, or has not been observed yet. Helper only.'),
    2: _('Has done it under supervision and it went fine. Leads simple jobs, calls for help on faults.'),
    3: _('Has led this work alone, repeatedly, with no callbacks.'),
    4: _('Others call on them when they are stuck. Can train others.'),
}
SKILL_LEVEL_SHORT = {
    1: _('Helper only'),
    2: _('Under supervision'),
    3: _('Alone, no callbacks'),
    4: _('Can train others'),
}
LABELLED_LEVEL_CHOICES = [('', '---------')] + [
    (level, format_lazy('{} — {}', level, SKILL_LEVEL_SHORT[level])) for level, _label in SKILL_LEVEL_CHOICES
]


class SelfRateLevelForm(forms.Form):
    """A technician's own first guess at a conduct-area level — for a
    skill, see SelfRateSkillForm below, which also requires evidence.
    """
    level = forms.ChoiceField(choices=LABELLED_LEVEL_CHOICES, label=_('Level'))


class SelfRateSkillForm(SelfRateLevelForm):
    """Same 1-4 scale as SelfRateLevelForm, plus proof: a skill self-rating
    only means something once a supervisor can see the technician actually
    doing the repair — smoothly and fast — not just take their word for it.
    Conduct areas (cleanliness, punctuality, ...) have no such evidence to
    show, so they stay on the plain form above.
    """
    evidence = forms.FileField(
        label=_('Photo or video of you performing this task'),
        widget=forms.FileInput(attrs={'accept': 'image/*,video/*'}),
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_SKILL_EVIDENCE_EXTENSIONS)],
    )
    note = forms.CharField(required=False, max_length=255, label=_('Note'))

    def clean_evidence(self):
        evidence = self.cleaned_data['evidence']
        if evidence.size > MAX_SKILL_EVIDENCE_UPLOAD_BYTES:
            raise forms.ValidationError(_('The file is too large. The maximum size is 25 MB.'))
        return evidence


class ReviewLevelForm(forms.Form):
    """A supervisor's confirmed level — always overwrites whatever was
    there, self-rated or previously supervisor-set.
    """
    level = forms.ChoiceField(choices=LABELLED_LEVEL_CHOICES, label=_('Level'))
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
    # Explicit choices, not TaskAttachment.Purpose.choices wholesale — the
    # delivery-note/written-report/other values further down belong to
    # StaffAttachmentUploadForm, not the technician's own fault-evidence
    # upload.
    purpose = forms.ChoiceField(
        choices=[
            (TaskAttachment.Purpose.FAULT, _('Fault')),
            (TaskAttachment.Purpose.SERIAL_PLATE, _('Serial plate')),
            (TaskAttachment.Purpose.BEFORE, _('Before')),
            (TaskAttachment.Purpose.AFTER, _('After')),
        ],
        label=_('What is this?'),
    )

    def clean_file(self):
        file = self.cleaned_data['file']
        if file.size > MAX_MEDIA_UPLOAD_BYTES:
            raise forms.ValidationError(_('The file is too large. The maximum size is 25 MB.'))
        return file


class StaffAttachmentUploadForm(forms.Form):
    """A document a supervisor, manager, or admin adds to a task as the
    need comes up — a delivery note, a written report, anything else —
    separate from the technician's own fault-evidence photos above, and
    separate from the fixed one-per-task quotation/factory offer/invoice/
    delivery note fields on the task itself. This is a repeatable list,
    for whenever there's more than one, or nothing dedicated already fits.
    """

    file = forms.FileField(
        label=_('Document'),
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_MEDIA_EXTENSIONS + ['pdf'])],
    )
    purpose = forms.ChoiceField(
        choices=[
            (TaskAttachment.Purpose.DELIVERY_NOTE, _('Delivery note')),
            (TaskAttachment.Purpose.WRITTEN_REPORT, _('Written report')),
            (TaskAttachment.Purpose.OTHER, _('Other')),
        ],
        label=_('What is this?'),
    )

    def clean_file(self):
        file = self.cleaned_data['file']
        if file.size > MAX_MEDIA_UPLOAD_BYTES:
            raise forms.ValidationError(_('The file is too large. The maximum size is 25 MB.'))
        return file


class PhotoSizeMixin:
    def clean_photo(self):
        photo = self.cleaned_data['photo']
        if photo and photo.size > MAX_PHOTO_UPLOAD_BYTES:
            raise forms.ValidationError(_('The photo is too large. The maximum size is 5 MB.'))
        return photo


class SkillCreateForm(forms.ModelForm):
    """A manager adding a repair task to the certification list — global,
    not scoped to any country or brand. Every other field of Skill
    (is_active) is left alone here; deactivating an existing one is a
    Django admin action for now, same as brands and task types.
    """

    class Meta:
        model = Skill
        fields = ['name', 'name_ar', 'category']


class BrandCreateForm(forms.ModelForm):
    """A manager adding an equipment brand/vendor — global, not scoped to
    any country. Same shape as SkillCreateForm: deactivating an existing
    one is a Django admin action for now.
    """

    class Meta:
        model = Brand
        fields = ['name', 'portal_url']


class ConductAreaCreateForm(forms.ModelForm):
    """A manager adding a non-technical professionalism area to the
    certification list — global, not scoped to any country. Same shape
    as SkillCreateForm: deactivating an existing one is a Django admin
    action for now.
    """

    class Meta:
        model = ConductArea
        fields = ['name', 'name_ar']


class CountryCreateForm(forms.ModelForm):
    """A manager adding a country the roster and every other per-country
    screen can scope to. Starts active; toggling that afterward, and
    deleting one no longer in use, happen from the Countries list itself.
    """

    WEEKDAY_CHOICES = [
        ('0', _('Monday')), ('1', _('Tuesday')), ('2', _('Wednesday')), ('3', _('Thursday')),
        ('4', _('Friday')), ('5', _('Saturday')), ('6', _('Sunday')),
    ]
    weekend_days = forms.MultipleChoiceField(
        choices=WEEKDAY_CHOICES, initial=['4', '5'], widget=forms.CheckboxSelectMultiple,
        label=_('Weekend days'), required=False,
    )

    class Meta:
        model = Country
        fields = ['name', 'name_ar', 'iso_code', 'task_prefix', 'timezone', 'currency_code', 'weekend_days', 'report_language']

    def clean_weekend_days(self):
        return ','.join(sorted(self.cleaned_data['weekend_days']))


class TechnicianCreateForm(forms.ModelForm):
    """Country comes from the supervisor creating it, set in the view —
    never a field here, same scoping every other per-country screen uses.
    No `user` field: same as a customer, the login is a separate step
    from the Edit screen afterward, not part of creation itself.
    """

    class Meta:
        model = Technician
        # Role first: this one screen adds every kind of staff account.
        fields = [
            'role', 'full_name', 'phone', 'language', 'employment_type',
            'has_transport', 'can_carry_large', 'hired_on',
        ]
        widgets = {
            'hired_on': forms.DateInput(attrs={'type': 'date'}),
        }


class TechnicianEditForm(PhotoSizeMixin, forms.ModelForm):
    """A supervisor or manager editing someone else's record, from the
    roster. Photo/name/phone/language/email are open to anyone with
    manage_technicians; country/employment details are manager-only HR
    decisions — same reasoning this already applied to country alone
    (the only other cross-country action, the active-country switcher,
    is manager-only too), now extended to the rest of the office-side
    fields. Role is narrower still — admin-only, not just manager — since
    it's a privilege grant: whoever creates a login next inherits
    whatever role is already sitting on the record, and technician_create
    is admin-only for exactly this reason, so a plain manager promoting
    someone to admin from here would just be the same escalation through
    a different door.

    Email lives on the linked auth user, not Technician, same pattern as
    MyProfileForm — but unlike a technician editing their own profile,
    the target here might have no login account yet, so the field is
    only shown/saved when one exists.
    """

    email = forms.EmailField(required=False, label=_('email'))

    class Meta:
        model = Technician
        fields = [
            'photo', 'full_name', 'phone', 'language', 'country', 'role',
            'employment_type', 'has_transport', 'can_carry_large', 'hired_on',
        ]
        widgets = {
            'photo': forms.ClearableFileInput(attrs={'accept': 'image/*'}),
            'hired_on': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, is_manager=True, is_admin=False, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.user_id:
            self.fields['email'].initial = self.instance.user.email
        else:
            del self.fields['email']
        if not is_manager:
            for field_name in ['country', 'role', 'employment_type', 'has_transport', 'can_carry_large', 'hired_on']:
                del self.fields[field_name]
        else:
            if 'country' in self.fields:
                self.fields['country'].queryset = Country.objects.filter(is_active=True)
            if not is_admin:
                del self.fields['role']

    def save(self, commit=True):
        technician = super().save(commit=commit)
        if commit and technician.user_id and 'email' in self.cleaned_data:
            technician.user.email = self.cleaned_data['email']
            technician.user.save(update_fields=['email'])
        return technician


class MyProfileForm(PhotoSizeMixin, forms.ModelForm):
    """A technician editing their own photo, language, phone, and email —
    their own contact details, never anyone else's decision to make. Role,
    country, and employment stay office-side changes.

    Email lives on the linked auth user, not Technician, so it's a plain
    field here rather than a Meta field, kept in sync with request.user
    manually in save().
    """

    email = forms.EmailField(required=False, label=_('Email'))

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
        help_text=_('e.g. gym closed, no key available, customer absent'),
    )


class PauseTaskForm(forms.Form):
    """Stopping for the day on a multi-day task — not blocked, just
    picking back up tomorrow. The note is optional; resuming needs
    nothing typed at all.
    """
    note = forms.CharField(
        label=_('Note'), required=False, widget=forms.Textarea(attrs={'rows': 2}),
        help_text=_('Optional: any notes required before work resumes.'),
    )


class CloseTaskForm(forms.Form):
    """Manager-only bypass of the normal report-approve pipeline, for a
    task that turns out not to need one — customer cancelled, issue
    resolved some other way, etc.
    """
    note = forms.CharField(
        label=_('Reason'), widget=forms.Textarea(attrs={'rows': 2}),
        help_text=_('Reason for closing without a report, for example a customer cancellation or resolution by '
                    'other means.'),
    )


class CancelTaskForm(forms.Form):
    """Manager-only — calls a task off before any report is filed. Unlike
    closing, the customer's ticket conversation stays open."""
    note = forms.CharField(
        label=_('Reason'), widget=forms.Textarea(attrs={'rows': 2}),
        help_text=_('Reason for cancellation, for example the customer cancelled the request.'),
    )


class NegligenceFlagForm(forms.Form):
    """Manager-only — marks a completed task as one the customer later
    complained about due to the lead's own negligence, not bad luck.
    Feeds the lead's reliability record; never shown to the technician.
    """
    note = forms.CharField(
        label=_('Description of the issue'), widget=forms.Textarea(attrs={'rows': 2}),
        help_text=_('Describe the customer complaint and explain why it constitutes negligence rather than an '
                    'unforeseeable circumstance.'),
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
    finding_code = forms.ChoiceField(choices=[('', '---------')] + Finding.choices, required=False, label=_('Finding'))
    note = forms.CharField(required=False, max_length=255, label=_('Other finding'))
    action_code = forms.ChoiceField(choices=[('', '---------')] + Action.choices, required=False, label=_('Action taken'))
    action = forms.CharField(required=False, max_length=255, label=_('Other action'))

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
                _('Select the machine and an outcome, or leave this row blank.'),
            )
        return cleaned


class NewAssetForm(forms.Form):
    """A machine not yet in the system — created here, per the doc's 'assets get created' step."""

    brand = forms.ModelChoiceField(
        queryset=Brand.objects.filter(is_active=True), required=False, label=_('Brand'),
    )
    model_name = forms.CharField(
        required=False, max_length=150, label=_('Model'), help_text=_('free text as shown on the plate'),
    )
    serial_no = forms.CharField(required=False, max_length=100, label=_('Serial number'))
    outcome = forms.ChoiceField(
        choices=[('', '---------')] + TaskAsset.Outcome.choices, required=False, label=_('Outcome'),
    )
    finding_code = forms.ChoiceField(choices=[('', '---------')] + Finding.choices, required=False, label=_('Finding'))
    note = forms.CharField(required=False, max_length=255, label=_('Other finding'))
    action_code = forms.ChoiceField(choices=[('', '---------')] + Action.choices, required=False, label=_('Action taken'))
    action = forms.CharField(required=False, max_length=255, label=_('Other action'))
    # The customer's ticket machine this row registers, when it came from
    # the ticket (it wasn't in their machine list).
    ticket_machine = forms.IntegerField(required=False, widget=forms.HiddenInput)

    def clean(self):
        cleaned = super().clean()
        if not any(cleaned.get(f) for f in ('brand', 'model_name', 'serial_no', 'outcome')):
            return cleaned
        if not cleaned.get('brand') or not cleaned.get('model_name') or not cleaned.get('outcome'):
            raise forms.ValidationError(
                _('Enter at least a brand, model, and outcome for each new machine, or leave the row blank.'),
            )
        return cleaned


class TaskProductForm(forms.Form):
    """One machine's line on an installation/loading task — product code
    mandatory, everything else optional. serial_number/quantity are the
    plain delivery-note fields; replacement/frame/arm/padding/trim/
    comment/note are the per-unit checklist a supervisor fills in for an
    actual machine (custom color per component, and what it got swapped
    for if the ordered one wasn't available) — left blank for a simple
    parts line that's just a code and a quantity.
    """

    product_code = forms.CharField(required=False, max_length=100, label=_('Product code'))
    serial_number = forms.CharField(required=False, max_length=100, label=_('Serial number'))
    quantity = forms.IntegerField(required=False, min_value=1, max_value=99999, label=_('Quantity'))
    replacement = forms.CharField(
        required=False, max_length=150, label=_('Replacement'),
        help_text=_('the item this replaced, if the ordered item was unavailable'),
    )
    frame = forms.CharField(required=False, max_length=100, label=_('Frame color'))
    arm = forms.CharField(required=False, max_length=100, label=_('Arm color'))
    padding = forms.CharField(required=False, max_length=100, label=_('Padding color'))
    trim = forms.CharField(required=False, max_length=100, label=_('Trim color'))
    comment = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}), label=_('Comment'))
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}), label=_('Note'))

    def clean(self):
        cleaned = super().clean()
        # Marked for deletion (can_delete=True on the formset) — skip the
        # product_code requirement entirely, so removing a returned/faulty
        # machine never demands re-filling the row just to clear it.
        if cleaned.get('DELETE'):
            return cleaned
        row_fields = (
            'product_code', 'serial_number', 'quantity', 'replacement',
            'frame', 'arm', 'padding', 'trim', 'comment', 'note',
        )
        if not any(cleaned.get(f) for f in row_fields):
            return cleaned
        if not cleaned.get('product_code'):
            raise forms.ValidationError(_('Enter a product code, or leave this row blank.'))
        if not cleaned.get('quantity'):
            cleaned['quantity'] = 1
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

    def has_changed(self, initial, data):
        # An empty list (nothing chosen) isn't a change — otherwise every
        # unused card in a formset looks filled in once any file is posted.
        return not self.disabled and bool(data)


class TicketAttachmentsForm(forms.Form):
    """The photos/video box shared by both ways a ticket is opened."""

    attachments = MultipleFileField(
        required=False, label=_('Photos and/or short video'),
        help_text=_('showing the issue and the serial number; up to %(count)d files, %(size)d MB each') % {
            'count': MAX_TICKET_ATTACHMENT_COUNT, 'size': MAX_TICKET_ATTACHMENT_BYTES // (1024 * 1024),
        },
        widget=MultipleFileInput(attrs={'multiple': True, 'accept': 'image/*,video/*'}),
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_TICKET_ATTACHMENT_EXTENSIONS)],
    )

    def clean_attachments(self):
        files = self.cleaned_data['attachments']
        if len(files) > MAX_TICKET_ATTACHMENT_COUNT:
            raise forms.ValidationError(
                _('A maximum of %(count)d files may be attached.') % {'count': MAX_TICKET_ATTACHMENT_COUNT},
            )
        for file in files:
            if file.size > MAX_TICKET_ATTACHMENT_BYTES:
                raise forms.ValidationError(_('Each file must be smaller than 25 MB. “%(name)s” exceeds this limit.') % {
                    'name': file.name,
                })
        return files


class SiteNameChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, site):
        return site.name


class CustomerPortalTicketForm(forms.ModelForm):
    """A logged-in customer reporting an issue at one of their own
    sites. The company name, site, and address are never typed here at
    all; a portal login already knows who they are and where their sites
    are (set directly in the view). The machines and their photos come
    from TicketMachineFormSet alongside it.
    """

    class Meta:
        model = CustomerTicket
        fields = ['contact_name', 'contact_phone', 'contact_email', 'shipping_address', 'notes']
        widgets = {
            'shipping_address': forms.Textarea(attrs={'rows': 2}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }
        labels = {'notes': _('Anything else we should know')}

    def __init__(self, *args, customer, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields = {'site': SiteNameChoiceField(queryset=customer.sites.all(), label=_('Site')), **self.fields}


class TicketMachineForm(forms.Form):
    """One machine card on the portal's ticket form: one of the
    customer's registered machines (grouped by site), or the model and
    serial of one that isn't in their list, plus what's wrong and photos.
    """

    asset = forms.ChoiceField(required=False, label=_('Machine'))
    model_name = forms.CharField(required=False, max_length=150, label=_('Model, if not in the list'))
    serial_no = forms.CharField(required=False, max_length=100, label=_('Serial number, if not in the list'))
    # The silver serial-number sticker on the machine. No `capture`: phones
    # then offer both "take photo" and "choose from library".
    serial_photo = forms.FileField(
        required=False, label=_('Or a photo of the serial number sticker'),
        help_text=_('the silver sticker on the machine: take a photo or upload one'),
        widget=forms.ClearableFileInput(attrs={'accept': 'image/*'}),
        validators=[FileExtensionValidator(allowed_extensions=SERIAL_PHOTO_EXTENSIONS)],
    )
    problem = forms.ChoiceField(
        choices=[('', _('Select…'))] + TicketMachine.Problem.choices, label=_('Problem'), required=False,
    )
    description = forms.CharField(required=False, label=_('Description'), widget=forms.Textarea(attrs={'rows': 2}))
    out_of_service = forms.BooleanField(required=False, label=_('The machine cannot be used'))
    files = MultipleFileField(
        required=False, label=_('Photos or a short video'),
        help_text=_('showing the issue and the serial number'),
        widget=MultipleFileInput(attrs={'multiple': True, 'accept': 'image/*,video/*'}),
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_TICKET_ATTACHMENT_EXTENSIONS)],
    )

    def __init__(self, *args, customer, **kwargs):
        super().__init__(*args, **kwargs)
        assets = Asset.objects.filter(site__customer=customer).exclude(status=Asset.Status.RETIRED).select_related(
            'site', 'brand',
        ).order_by('site__name', 'brand__name', 'model_name')
        self.assets = {str(asset.pk): asset for asset in assets}
        groups = {}
        for asset in assets:
            label = f"{asset.brand.name + ' ' if asset.brand_id else ''}{asset.model_name}"
            if asset.serial_no:
                label += f' ({asset.serial_no})'
            groups.setdefault(asset.site.name, []).append((str(asset.pk), label))
        self.fields['asset'].choices = [('', _('Not in the list'))] + list(groups.items())

    def clean_asset(self):
        value = self.cleaned_data['asset']
        return self.assets.get(value) if value else None

    def clean_files(self):
        files = self.cleaned_data['files'] or []
        for file in files:
            if file.size > MAX_TICKET_ATTACHMENT_BYTES:
                raise forms.ValidationError(_('Each file must be smaller than 25 MB. “%(name)s” exceeds this limit.') % {
                    'name': file.name,
                })
        return files

    def clean(self):
        cleaned = super().clean()
        if not self.has_changed():
            return cleaned
        if not cleaned.get('asset'):
            if not cleaned.get('model_name'):
                self.add_error('asset', _('Please choose the machine, or enter its model if it is not in the list.'))
            if not cleaned.get('serial_no') and not cleaned.get('serial_photo'):
                self.add_error(
                    'serial_no', _('Please enter the serial number, or add a photo of the serial number sticker.'),
                )
        photo = cleaned.get('serial_photo')
        if photo and photo.size > MAX_TICKET_ATTACHMENT_BYTES:
            self.add_error('serial_photo', _('Each file must be smaller than 25 MB. “%(name)s” exceeds this limit.') % {
                'name': photo.name,
            })
        if not cleaned.get('problem'):
            self.add_error('problem', _('Please select the problem.'))
        return cleaned


class BaseTicketMachineFormSet(forms.BaseFormSet):
    """The machine cards together: at least one, at most
    MAX_TICKET_MACHINES, and the ticket's overall file limit across them.
    """

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        filled = [form for form in self.forms if form.has_changed()]
        if not filled:
            raise forms.ValidationError(_('Please add at least one machine.'))
        if sum(len(form.cleaned_data.get('files') or []) for form in filled) > MAX_TICKET_ATTACHMENT_COUNT:
            raise forms.ValidationError(
                _('A maximum of %(count)d files may be attached.') % {'count': MAX_TICKET_ATTACHMENT_COUNT},
            )


TicketMachineFormSet = forms.formset_factory(
    TicketMachineForm, formset=BaseTicketMachineFormSet,
    extra=MAX_TICKET_MACHINES, max_num=MAX_TICKET_MACHINES, validate_max=True,
)


class SiteChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, site):
        return f'{site.customer.name} — {site.name}'


class StaffTicketForm(TicketAttachmentsForm, forms.ModelForm):
    """Staff opening a ticket for a customer who phoned, messaged or
    emailed (open_tickets) — the same ticket a customer would submit from
    their portal, so it goes through the same review, and the customer can
    follow it and reply there. Contact fields left blank fall back to the
    site's (or customer's) own contact.
    """

    site = SiteChoiceField(queryset=Site.objects.none(), label=_('Customer and site'))
    source = forms.ChoiceField(
        label=_('How the customer reached us'),
        choices=[
            (value, label) for value, label in CustomerTicket.Source.choices
            if value != CustomerTicket.Source.PORTAL
        ],
    )

    class Meta:
        model = CustomerTicket
        fields = [
            'site', 'source', 'contact_name', 'contact_phone', 'contact_email', 'shipping_address',
            'serial_numbers', 'description', 'notes',
        ]
        widgets = {
            'shipping_address': forms.Textarea(attrs={'rows': 2}),
            'serial_numbers': forms.Textarea(attrs={'rows': 3, 'placeholder': 'SN-12345\nSN-67890'}),
            'description': forms.Textarea(attrs={'rows': 5}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, country, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['site'].queryset = (
            Site.objects.filter(customer__country=country, customer__is_active=True)
            .select_related('customer').order_by('customer__name', 'name')
        )
        for name in ('contact_name', 'contact_phone'):
            self.fields[name].required = False
            self.fields[name].help_text = _("Leave blank to use the site's own contact details.")
        # On a phone call the serials often aren't known yet.
        self.fields['serial_numbers'].required = False


class InternalTicketForm(TicketAttachmentsForm, forms.ModelForm):
    """A stock request, vendor follow-up or other internal matter — no
    customer, nothing ever sent to one. Goes to whoever it's assigned to,
    who follows it up with internal notes until it's closed.
    """

    assigned_to = forms.ModelChoiceField(
        queryset=Technician.objects.none(), label=_('For'),
        help_text=_('The person responsible for handling it. They will be notified, and it will appear in their '
                    'internal tickets.'),
    )

    class Meta:
        model = CustomerTicket
        fields = ['subject', 'vendor_name', 'description', 'assigned_to']
        labels = {'description': _('Details')}
        help_texts = {'description': ''}
        widgets = {'description': forms.Textarea(attrs={'rows': 5})}

    def __init__(self, *args, kind, country, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['subject'].required = True
        self.fields['subject'].help_text = ''
        if kind == CustomerTicket.Kind.VENDOR:
            self.fields['vendor_name'].required = True
        else:
            del self.fields['vendor_name']
        self.fields['assigned_to'].queryset = Technician.objects.filter(
            is_active=True, country=country,
        ).exclude(role=Technician.Role.TECHNICIAN).order_by('full_name')
        self.fields['assigned_to'].label_from_instance = (
            lambda technician: f'{technician.full_name} — {technician.get_role_display()}'
        )


class InternalTicketNoteForm(forms.Form):
    message = forms.CharField(label=_('Add a note'), widget=forms.Textarea(attrs={'rows': 3}))


class ReassignInternalTicketForm(forms.Form):
    assigned_to = forms.ModelChoiceField(queryset=Technician.objects.none(), label=_('Reassign to'))

    def __init__(self, *args, country, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_to'].queryset = Technician.objects.filter(
            is_active=True, country=country,
        ).exclude(role=Technician.Role.TECHNICIAN).order_by('full_name')


class DismissTicketForm(forms.Form):
    dismissal_reason = forms.CharField(
        label=_('Reason'), widget=forms.Textarea(attrs={'rows': 3}),
        help_text=_('Reason this ticket will not become a task, for example spam, a duplicate or outside our scope.'),
    )


class CloseTicketForm(forms.Form):
    """Separate from DismissTicketForm — a legitimately resolved issue
    that just never needed a task, not an invalid one.
    """
    close_reason = forms.CharField(
        label=_('Reason'), widget=forms.Textarea(attrs={'rows': 3}),
        help_text=_('How the matter was resolved without a task, for example advice given or resolved by phone.'),
    )


class TicketReplyForm(forms.Form):
    """One message — same shape for staff replying and a customer
    replying back, on either the staff review screen or the customer's
    own portal. Which side sent it is recorded by the view, not here.
    """
    message = forms.CharField(label=_('Reply'), widget=forms.Textarea(attrs={'rows': 3}))
    attachment = forms.FileField(
        required=False, label=_('Photo, video, or PDF'),
        help_text=_('up to %(size)d MB') % {'size': MAX_TICKET_ATTACHMENT_BYTES // (1024 * 1024)},
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_TICKET_ATTACHMENT_EXTENSIONS)],
    )

    def clean_attachment(self):
        attachment = self.cleaned_data['attachment']
        if attachment and attachment.size > MAX_TICKET_ATTACHMENT_BYTES:
            raise forms.ValidationError(_('The file is too large. The maximum size is 25 MB.'))
        return attachment


class TicketInternalNoteForm(forms.Form):
    """A manager-tier-only progress note — never seen by the customer or
    by a supervisor/technician, unlike TicketReplyForm.
    """
    message = forms.CharField(label=_('Note'), widget=forms.Textarea(attrs={'rows': 3}))
    attachment = forms.FileField(
        required=False, label=_('Attachment'),
        help_text=_('up to %(size)d MB') % {'size': MAX_TICKET_ATTACHMENT_BYTES // (1024 * 1024)},
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_TICKET_ATTACHMENT_EXTENSIONS)],
    )

    def clean_attachment(self):
        attachment = self.cleaned_data['attachment']
        if attachment and attachment.size > MAX_TICKET_ATTACHMENT_BYTES:
            raise forms.ValidationError(_('The file is too large. The maximum size is 25 MB.'))
        return attachment


class TaskMessageForm(forms.Form):
    """A note posted to a task's own thread — text only, no attachment;
    photos already have their own place (the fault-evidence upload).
    """
    message = forms.CharField(label=_('Message'), widget=forms.Textarea(attrs={'rows': 2}))


class TicketLogisticsForm(forms.ModelForm):
    """Both optional, filled in later once parts have actually shipped —
    never known at submission time, so not part of CustomerPortalTicketForm.
    """

    class Meta:
        model = CustomerTicket
        fields = ['pak_reference_number', 'shipping_company', 'shipping_tracking_number']


class TicketEditForm(forms.ModelForm):
    """Correcting what the customer typed — a mis-heard phone number, a
    typo in the site description, and so on. Country isn't here: moving
    a ticket to a different country is a relocation, not a correction,
    same reasoning Technician/Customer country changes already use.
    """

    class Meta:
        model = CustomerTicket
        fields = [
            'company_name', 'customer_code', 'site_description', 'site_address',
            'contact_name', 'contact_phone', 'contact_email', 'shipping_address',
            'serial_numbers', 'description', 'notes',
        ]
        widgets = {
            'site_address': forms.Textarea(attrs={'rows': 2}),
            'shipping_address': forms.Textarea(attrs={'rows': 2}),
            'serial_numbers': forms.Textarea(attrs={'rows': 3}),
            'description': forms.Textarea(attrs={'rows': 5}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }


class AssignTicketForm(forms.Form):
    """Who's handling this ticket — any active technician, in its own
    country, whose role currently has manage_tickets — not necessarily
    the person who'll ultimately convert or dismiss it. Driven by the
    same RolePermission table ticket_review itself checks, not a
    hardcoded role list, so it always matches whichever role (or roles)
    Roles & Permissions currently grants ticket access to.
    """
    assigned_to = forms.ModelChoiceField(queryset=Technician.objects.none(), label=_('Assign to'))

    def __init__(self, *args, country=None, **kwargs):
        super().__init__(*args, **kwargs)
        ticket_manager_roles = RolePermission.objects.filter(
            permission=RolePermission.Permission.MANAGE_TICKETS, allowed=True,
        ).values_list('role', flat=True)
        self.fields['assigned_to'].queryset = Technician.objects.filter(
            is_active=True, country=country, role__in=list(ticket_manager_roles),
        ).order_by('full_name')


class CorrectEventTimeForm(forms.Form):
    """A manager/admin correcting when a tap really happened. The window it
    must fall in (after the previous event, before the next, never in the
    future) comes from the view, which knows the neighbouring events.
    """

    corrected_at = forms.DateTimeField(
        label=_('Correct time'), input_formats=[DATETIME_INPUT_FORMAT],
        widget=forms.DateTimeInput(format=DATETIME_INPUT_FORMAT, attrs={'type': 'datetime-local'}),
    )
    reason = forms.CharField(label=_('Reason'), widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, earliest=None, latest=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.earliest = earliest
        self.latest = latest

    def clean_corrected_at(self):
        value = self.cleaned_data['corrected_at']
        if value > timezone.now():
            raise forms.ValidationError(_('A corrected time cannot be in the future.'))
        if self.earliest and value < self.earliest:
            raise forms.ValidationError(_('This is earlier than the previous step on this task.'))
        if self.latest and value > self.latest:
            raise forms.ValidationError(_('This is later than the next step on this task.'))
        return value


def escalation_deciders(escalated_by, country):
    """Who this person can escalate a ticket to: only the next role up the
    chain (Technician.escalates_to_role), and only if that role can decide
    escalations (Roles & permissions). A role that handles every country's
    tickets is reachable wherever it's based; otherwise only someone in
    the ticket's own country.
    """
    next_role = escalated_by.escalates_to_role
    allowed = set(
        RolePermission.objects.filter(role=next_role, allowed=True).values_list('permission', flat=True),
    )
    if next_role is None or RolePermission.Permission.DECIDE_ESCALATED_TICKETS not in allowed:
        return Technician.objects.none()
    deciders = Technician.objects.filter(is_active=True, role=next_role).exclude(pk=escalated_by.pk)
    if RolePermission.Permission.TICKETS_ALL_COUNTRIES not in allowed:
        deciders = deciders.filter(country=country)
    return deciders.order_by('full_name')


class EscalateTicketForm(forms.Form):
    escalated_to = forms.ModelChoiceField(queryset=Technician.objects.none(), label=_('Escalate to'))
    reason = forms.CharField(
        label=_('Reason (internal)'), widget=forms.Textarea(attrs={'rows': 3}),
        help_text=_('Never shown to the customer.'),
    )
    customer_message = forms.CharField(
        required=False, label=_('Message to the customer (optional)'), widget=forms.Textarea(attrs={'rows': 2}),
        help_text=_("Displayed on the customer's ticket page under “escalated to our management team”."),
    )

    def __init__(self, *args, country=None, escalated_by=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['escalated_to'].queryset = escalation_deciders(escalated_by, country)


class DecideEscalationForm(forms.Form):
    decision = forms.ChoiceField(
        choices=[
            (value, label) for value, label in TicketEscalation.Decision.choices
            if value not in (TicketEscalation.Decision.PENDING, TicketEscalation.Decision.FORWARDED)
        ],
        widget=forms.RadioSelect, label=_('Decision'),
    )
    decision_note = forms.CharField(
        label=_('Note for the support desk (internal)'), widget=forms.Textarea(attrs={'rows': 3}),
        help_text=_('Never shown to the customer.'),
    )


class TechnicianTripForm(forms.ModelForm):
    """A manager recording a trip abroad (docs: technician_trip)."""

    class Meta:
        model = TechnicianTrip
        fields = ['country', 'start_date', 'end_date', 'note']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, home_country=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['country'].queryset = Country.objects.filter(is_active=True).exclude(
            pk=getattr(home_country, 'pk', None),
        ).order_by('name')

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('start_date'), cleaned.get('end_date')
        if start and end and end < start:
            self.add_error('end_date', _('The trip cannot end before it starts.'))
        return cleaned


class PartForm(forms.ModelForm):
    class Meta:
        model = Part
        fields = ['code', 'description']

    def clean_code(self):
        code = self.cleaned_data['code'].strip().upper()
        if Part.objects.filter(code=code).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_('This code is already in the list.'))
        return code


class PartImportForm(forms.Form):
    file = forms.FileField(
        label=_('CSV file'), help_text=_('Two columns: code and description. A header row is permitted.'),
        validators=[FileExtensionValidator(allowed_extensions=['csv'])],
    )


class NotificationSettingsForm(forms.ModelForm):
    """The admin's Notifications page: one switch per automatic message,
    and which roles get the new-ticket and new-task emails.
    """

    new_ticket_roles = forms.MultipleChoiceField(
        choices=Technician.Role.choices, required=False, widget=forms.CheckboxSelectMultiple,
        label=_('Who receives it'),
    )
    new_task_roles = forms.MultipleChoiceField(
        choices=Technician.Role.choices, required=False, widget=forms.CheckboxSelectMultiple,
        label=_('Who receives it'),
    )

    class Meta:
        model = NotificationSettings
        fields = [
            'ticket_received_email', 'reply_to_customer_email', 'auto_notify_on_reschedule',
            'new_ticket_email', 'new_ticket_roles', 'new_task_email', 'new_task_roles',
            'reply_to_staff_email', 'escalation_emails', 'shipment_to_supervisor_email',
            'push_assigned', 'push_removed', 'push_cancelled',
        ]

    def clean(self):
        cleaned = super().clean()
        for switch, roles in (('new_ticket_email', 'new_ticket_roles'), ('new_task_email', 'new_task_roles')):
            if cleaned.get(switch) and not cleaned.get(roles):
                self.add_error(roles, _('Choose at least one role, or turn this email off.'))
        return cleaned
