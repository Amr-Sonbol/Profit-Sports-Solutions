import csv
import io
from datetime import date

from axes.utils import reset as axes_reset
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.conf import settings
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from people.models import RolePermission, Technician
from people.permissions import get_active_country, require_admin, require_manager, require_permission, scoped_or_404
from reference.models import Country
from tasks.forms import ChangePasswordForm, CustomerPortalTicketForm, NewPasswordForm, TicketMachineFormSet
from tasks.models import CustomerTicket, CustomerTicketAttachment, Task, TicketMachine, TicketNotification
from tasks.views import (
    save_new_ticket, send_new_ticket_email_to_staff, send_password_changed_email, send_ticket_confirmation_to_customer,
    ticket_machines_text,
)

from .accounts import (
    LINK_VALID_DAYS, AccountStatus, account_status, email_change_for_token, locked_usernames, log_account_action,
    send_email_change_link, send_email_changed_notice, send_password_link, send_welcome, user_for_link_token,
)
from .forms import (
    ChangeLoginEmailForm, CreateLoginForm, CustomerCreateForm, CustomerEditForm, CustomerFirstLoginForm,
    CustomerImportForm, CustomerMachineForm, CustomerSettingsForm, DeactivateCustomerForm, PortalColorForm,
    PortalEmailChangeForm, PortalLoginForm, PortalPasswordHelpForm, QuickInviteForm,
    SiteCreateForm, SiteEditForm,
)
from .models import Asset, Customer, Site
from . import notify
from .progress import (
    DONE_STATUSES, is_booked, is_upcoming, progress_steps, ticket_progress, visit_day, visit_progress,
)

# Only the name is required: a file of just customer_name and
# customer_contact_email is enough to invite everyone, and each customer
# completes the rest themselves at first sign-in.
CUSTOMER_IMPORT_REQUIRED_COLUMNS = ['customer_name']
CUSTOMER_IMPORT_COLUMNS = CUSTOMER_IMPORT_REQUIRED_COLUMNS + [
    'customer_contact_email', 'segment', 'customer_contact_name', 'customer_contact_phone',
    'site_name', 'site_address', 'site_contact_name', 'site_contact_phone', 'site_contact_email', 'access_notes',
]

# Avoids characters easy to mis-type or mis-read out loud/over chat: 0/O, 1/l/I.
TEMPORARY_PASSWORD_ALPHABET = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789'


def _generate_temporary_password():
    return get_random_string(12, allowed_chars=TEMPORARY_PASSWORD_ALPHABET)


@login_required
def customer_list(request):
    """Every customer in the viewer's active country — gyms, hotels, clubs,
    whatever "other" turns out to mean — with a way to add a new one.
    """
    require_permission(request, RolePermission.Permission.MANAGE_CUSTOMERS)

    show_inactive = request.GET.get('status') == 'inactive'
    search = request.GET.get('q', '').strip()
    customers = Customer.objects.filter(
        is_active=not show_inactive, country=get_active_country(request),
    ).prefetch_related('sites')
    if search:
        customers = customers.filter(name__icontains=search)

    return render(request, 'customers/customer_list.html', {
        'customers': customers, 'search': search, 'show_inactive': show_inactive,
    })


@login_required
def all_customers(request):
    """Every customer in every country — a manager's combined roster, same
    fixed floor as all_technicians. Reaching one from here opens
    the same customer_detail/edit screens, which allow manager-tier
    cross-country access for exactly this reason.
    """
    require_manager(request)

    search = request.GET.get('q', '').strip()
    customers = Customer.objects.filter(is_active=True).select_related('country').prefetch_related('sites')
    if search:
        customers = customers.filter(name__icontains=search)

    return render(request, 'customers/all_customers.html', {'customers': customers, 'search': search})


@login_required
def customer_create(request):
    """Admin-only — same reasoning as tasks.views.technician_create:
    creating the record is the one place a country gets assigned to it,
    and letting manage_customers alone reach this (a permission the
    supervisor role has by default) put customer creation in more hands
    than intended.
    """
    require_admin(request)

    if request.method == 'POST':
        form = CustomerCreateForm(request.POST)
        if form.is_valid():
            customer = form.save(commit=False)
            customer.country = get_active_country(request)
            customer.save()
            messages.success(request, _('Customer added.'))
            return redirect('customers:customer_detail', pk=customer.pk)
    else:
        form = CustomerCreateForm()

    return render(request, 'customers/customer_create.html', {'form': form})


@login_required
def customer_detail(request, pk):
    """The customer's sites, and — admin-only, same reasoning as
    customer_create — a way to add another one. A manager or admin
    reaches any customer regardless of active country (the point of
    all_customers); everyone else stays scoped to it, as before.
    """
    requesting_technician = require_permission(request, RolePermission.Permission.MANAGE_CUSTOMERS)
    is_admin = requesting_technician.role == Technician.Role.ADMIN
    customer = scoped_or_404(Customer.objects, pk, requesting_technician, get_active_country(request), 'country')

    form = SiteCreateForm(customer=customer) if is_admin else None
    if request.method == 'POST' and is_admin:
        form = SiteCreateForm(request.POST, customer=customer)
        if form.is_valid():
            site = form.save(commit=False)
            site.customer = customer
            site.save()
            messages.success(request, _('Site added.'))
            return redirect('customers:customer_detail', pk=customer.pk)

    context = {'customer': customer, 'sites': customer.sites.all(), 'form': form, 'is_admin': is_admin}
    return render(request, 'customers/customer_detail.html', context)


@login_required
def customer_edit(request, pk):
    """Everything about an existing customer in one place — their own
    details, their portal login (create it, reset its password), and
    deactivating them. A manager or admin reaches any customer regardless
    of active country, same reasoning as customer_detail.
    """
    requesting_technician = require_permission(request, RolePermission.Permission.MANAGE_CUSTOMERS)
    is_manager = requesting_technician.is_manager_tier
    customer = scoped_or_404(Customer.objects, pk, requesting_technician, get_active_country(request), 'country')

    form = CustomerEditForm(instance=customer)
    login_form = CreateLoginForm() if is_manager and customer.user_id is None else None
    password_form = NewPasswordForm(user=customer.user) if is_manager and customer.user_id else None
    deactivate_form = DeactivateCustomerForm()

    if request.method == 'POST' and request.POST.get('action') == 'create_login' and login_form is not None:
        login_form = CreateLoginForm(request.POST)
        if login_form.is_valid():
            email = login_form.cleaned_data['email']
            send_welcome(request, customer, email=email)
            log_account_action(request, customer, 'Portal account created; welcome email sent')
            messages.success(request, _(
                'A welcome email with a secure activation link has been sent to %(email)s.',
            ) % {'email': email})
            return redirect('customers:customer_edit', pk=customer.pk)

    elif request.method == 'POST' and request.POST.get('action') == 'reset_password' and password_form is not None:
        password_form = NewPasswordForm(user=customer.user, data=request.POST)
        if password_form.is_valid():
            password_form.save()
            # Temporary, same as a technician's: they choose their own next time.
            customer.must_change_password = True
            customer.save(update_fields=['must_change_password'])
            log_account_action(request, customer, 'Portal temporary password set')
            messages.success(request, _('The password has been reset. They will be asked to choose their own '
                                        'at their next sign-in.'))
            return redirect('customers:customer_edit', pk=customer.pk)

    elif request.method == 'POST' and request.POST.get('action') == 'deactivate' and is_manager:
        deactivate_form = DeactivateCustomerForm(request.POST)
        if deactivate_form.is_valid():
            customer.set_active(False, reason=deactivate_form.cleaned_data['reason'])
            messages.success(request, _('Customer deactivated.'))
            return redirect('customers:customer_list')

    elif request.method == 'POST' and request.POST.get('action') == 'reactivate' and is_manager:
        customer.set_active(True)
        messages.success(request, _('Customer reactivated.'))
        return redirect('customers:customer_edit', pk=customer.pk)

    elif request.method == 'POST':
        form = CustomerEditForm(request.POST, instance=customer)
        if form.is_valid():
            form.save()
            # The login email stays in lockstep with the business contact
            # email whenever there is one — one address to keep current,
            # not two that can quietly drift apart.
            if customer.user_id and customer.contact_email and customer.user.email != customer.contact_email:
                customer.user.email = customer.contact_email
                customer.user.save(update_fields=['email'])
            messages.success(request, _('Customer updated.'))
            return redirect('customers:customer_detail', pk=customer.pk)

    context = {
        'customer': customer, 'form': form, 'login_form': login_form,
        'password_form': password_form, 'deactivate_form': deactivate_form, 'is_manager': is_manager,
    }
    return render(request, 'customers/customer_edit.html', context)


@login_required
def site_edit(request, pk):
    require_permission(request, RolePermission.Permission.MANAGE_CUSTOMERS)
    site = get_object_or_404(Site, pk=pk, customer__country=get_active_country(request))

    if request.method == 'POST':
        form = SiteEditForm(request.POST, instance=site)
        if form.is_valid():
            form.save()
            messages.success(request, _('Site updated.'))
            return redirect('customers:customer_detail', pk=site.customer_id)
    else:
        form = SiteEditForm(instance=site)

    return render(request, 'customers/site_edit.html', {'site': site, 'form': form})


def _import_customers_csv(csv_file, country):
    """One row per site; rows sharing the same customer_name (case-
    insensitive) share one Customer, so a chain's several branches fit
    in one file. A row without a site just adds the customer — name and
    email are enough to send the welcome email; the customer completes
    the rest. Matches an existing customer/site by name instead of
    duplicating it, so the same file can be re-run safely — and an
    existing customer with no email yet picks one up from the file.
    """
    reader = csv.DictReader(io.TextIOWrapper(csv_file, encoding='utf-8-sig'))
    missing_columns = [col for col in CUSTOMER_IMPORT_REQUIRED_COLUMNS if col not in (reader.fieldnames or [])]
    if missing_columns:
        return None, _('Missing required column(s): %(columns)s') % {'columns': ', '.join(missing_columns)}

    valid_segments = {value for value, _label in Customer.Segment.choices}
    results = []
    customers_by_name = {}

    for row_number, row in enumerate(reader, start=2):
        customer_name = (row.get('customer_name') or '').strip()
        site_name = (row.get('site_name') or '').strip()
        site_address = (row.get('site_address') or '').strip()
        result = {'row': row_number, 'customer_name': customer_name or '—', 'site_name': site_name or '—'}

        if not customer_name:
            results.append({**result, 'outcome': 'error', 'detail': _('customer_name is required')})
            continue
        if site_name and not site_address:
            results.append({
                **result, 'outcome': 'error', 'detail': _('site_address is required when site_name is given'),
            })
            continue

        segment = (row.get('segment') or '').strip().lower() or Customer.Segment.GYM
        if segment not in valid_segments:
            results.append({
                **result, 'outcome': 'error',
                'detail': _('Unknown segment "%(segment)s". Please use gym, hotel, club or other.') % {'segment': segment},
            })
            continue

        try:
            with transaction.atomic():
                key = customer_name.lower()
                customer = customers_by_name.get(key)
                created_customer = False
                if customer is None:
                    customer = Customer.objects.filter(country=country, name__iexact=customer_name).first()
                    if customer is None:
                        customer = Customer(
                            country=country, name=customer_name, segment=segment,
                            contact_name=(row.get('customer_contact_name') or '').strip(),
                            contact_phone=(row.get('customer_contact_phone') or '').strip(),
                            contact_email=(row.get('customer_contact_email') or '').strip(),
                        )
                        customer.full_clean()
                        customer.save()
                        created_customer = True
                    customers_by_name[key] = customer

                if not site_name:
                    email = (row.get('customer_contact_email') or '').strip()
                    if created_customer:
                        detail, outcome = _('New customer created'), 'created'
                    elif email and not customer.contact_email:
                        customer.contact_email = email
                        customer.full_clean()
                        customer.save(update_fields=['contact_email'])
                        detail, outcome = _('Contact email added to existing customer'), 'created'
                    else:
                        detail, outcome = _('Customer already exists'), 'skipped'
                    results.append({**result, 'outcome': outcome, 'detail': detail})
                    continue

                if Site.objects.filter(customer=customer, name__iexact=site_name).exists():
                    results.append({
                        **result, 'outcome': 'skipped',
                        'detail': _('This customer already has a site with this name'),
                    })
                    continue

                site = Site(
                    customer=customer, name=site_name, address=site_address,
                    contact_name=(row.get('site_contact_name') or '').strip(),
                    contact_phone=(row.get('site_contact_phone') or '').strip(),
                    contact_email=(row.get('site_contact_email') or '').strip(),
                    access_notes=(row.get('access_notes') or '').strip(),
                )
                site.full_clean(exclude=['customer'])
                site.save()
        except (ValidationError, IntegrityError) as exc:
            detail = '; '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            results.append({**result, 'outcome': 'error', 'detail': detail})
            continue

        results.append({
            **result, 'outcome': 'created',
            'detail': _('New customer created') if created_customer else _('Added to existing customer'),
        })

    return results, None


@login_required
def customer_import(request):
    """Bulk-add customers and their sites from a CSV export — for
    getting an existing gym list into the app at once instead of
    one-by-one. Admin-only, same as customer_create — a bad file could
    create a lot of rows fast, through the same door.
    """
    require_admin(request)

    results = None
    if request.method == 'POST':
        form = CustomerImportForm(request.POST, request.FILES)
        if form.is_valid():
            results, file_error = _import_customers_csv(
                form.cleaned_data['csv_file'], get_active_country(request),
            )
            if file_error:
                messages.error(request, file_error)
                results = None
    else:
        form = CustomerImportForm()

    return render(request, 'customers/customer_import.html', {'form': form, 'results': results})


@login_required
def customer_import_template(request):
    require_manager(request)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="customers_template.csv"'
    writer = csv.writer(response)
    writer.writerow(CUSTOMER_IMPORT_COLUMNS)
    # Name and email only — the customer completes the rest after the welcome email.
    writer.writerow(['Gold Gym', 'info@goldgym.example', '', '', '', '', '', '', '', '', ''])
    # Or with sites: one row per branch, sharing the customer name.
    writer.writerow([
        'Fitness First', 'ali@example.com', 'gym', 'Ali Manager', '0501234567',
        'Marina Branch', 'Dubai Marina, near the mall', '', '', '', 'Gate code 1234',
    ])
    writer.writerow(['Fitness First', '', 'gym', '', '', 'JBR Branch', 'JBR, near the beach', '', '', '', ''])
    return response


def require_customer(request):
    """Restrict a view to a customer's own portal login — the customer-
    side counterpart to people.permissions.require_technician."""
    customer = getattr(request.user, 'customer', None)
    if customer is None:
        raise PermissionDenied
    return customer


def _portal_landing(user):
    """Where a customer login lands right after signing in — the one-time
    setup screen if they're still on a temporary password, their account
    home otherwise.
    """
    if user.customer.must_change_password:
        return redirect('customers:portal_first_login')
    return redirect('customers:portal_home')


def portal_login(request):
    """A customer's own login page — same underlying auth as the staff
    one (django.contrib.auth), just branded for them and rejecting
    anything that isn't a customer account, like a staff login typed in
    here by mistake — with the same message as a wrong password
    (PortalLoginForm), so it never says which accounts exist.
    """
    if request.user.is_authenticated:
        return _portal_landing(request.user) if hasattr(request.user, 'customer') else redirect('home')

    next_url = request.POST.get('next') or request.GET.get('next') or ''

    form = PortalLoginForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        if hasattr(user, 'customer'):
            login(request, user)
            if user.customer.must_change_password:
                return redirect('customers:portal_first_login')
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
            return redirect('customers:portal_home')
        form.add_error(None, form.error_messages['invalid_login'])

    return render(request, 'customers/portal_login.html', {'form': form, 'next': next_url})


SETUP_FIELDS = ['contact_name', 'contact_phone', 'contact_email', 'billing_address', 'shipping_address']


def _setup_initial(customer):
    return {field: getattr(customer, field) for field in SETUP_FIELDS}


def _complete_setup(customer, user, form):
    """Save what the customer filled in at first sign-in and their new
    password; the portal opens normally from then on. `user` is the very
    object the session holds, so its new password hash is the one the
    session is re-keyed to."""
    for field in SETUP_FIELDS:
        setattr(customer, field, form.cleaned_data[field])
    customer.must_change_password = False
    customer.save(update_fields=SETUP_FIELDS + ['must_change_password'])
    user.set_password(form.cleaned_data['new_password1'])
    user.save(update_fields=['password'])


@login_required(login_url='customers:portal_login')
def portal_first_login(request):
    """A one-time stop between signing in on a temporary password and
    reaching the rest of the portal — set a real password and confirm
    contact details staff may not have had (a bulk import, say). Once
    submitted, must_change_password clears and every later sign-in goes
    straight through, same as it always did.
    """
    customer = require_customer(request)
    if not customer.must_change_password:
        return redirect('customers:portal_home')

    if request.method == 'POST':
        form = CustomerFirstLoginForm(request.POST, user=request.user)
        if form.is_valid():
            _complete_setup(customer, request.user, form)
            update_session_auth_hash(request, request.user)
            messages.success(request, _('Your account is ready. Welcome.'))
            return redirect('customers:portal_home')
    else:
        form = CustomerFirstLoginForm(user=request.user, initial=_setup_initial(customer))

    return render(request, 'customers/portal_first_login.html', {'form': form})


def _portal_customer(request):
    """The signed-in customer, or a redirect to finish their first sign-in."""
    customer = require_customer(request)
    return customer, redirect('customers:portal_first_login') if customer.must_change_password else None


def _portal_requests(customer):
    """The customer's requests (tickets), newest first, each with its
    progress in their words (customers.progress), whether it's open, and
    the visit whose report it has (`report_visit`), if any."""
    tickets = list(customer.tickets.select_related('task__report').order_by('-submitted_at'))
    for ticket in tickets:
        ticket.report_visit = ticket.task if ticket.task_id and getattr(ticket.task, 'report', None) else None
        ticket.progress = ticket_progress(ticket)
        ticket.steps = progress_steps(ticket.progress) if ticket.heads_for_a_visit else None
        ticket.is_open = ticket.status == CustomerTicket.Status.NEW or (
            ticket.status == CustomerTicket.Status.CONVERTED and ticket.task_id and is_upcoming(ticket.task)
        )
    return tickets


def _portal_visits(customer):
    """The customer's visits as (coming up, history): coming up soonest
    first and those without a date yet last; history newest first, each
    with its report PDF (the customer-facing document emailed to them, no
    hours or prices) and whether it's waiting for their rating."""
    from reports.pdf import changed_after_signing

    visits = list(Task.objects.filter(site__customer=customer).select_related('site', 'task_type', 'report', 'feedback'))
    for visit in visits:
        report = getattr(visit, 'report', None)
        feedback = getattr(visit, 'feedback', None)
        visit.to_rate = report is not None and visit.status in DONE_STATUSES and not (feedback and feedback.submitted_at)
        visit.rating = feedback.rating if feedback and feedback.submitted_at else None
        visit.has_report = report is not None
        visit.report_changed = report is not None and changed_after_signing(report)
        visit.progress = visit_progress(visit)
        visit.day = visit_day(visit)
        if is_upcoming(visit) and visit.day and visit.day < timezone.localdate():
            # Its day passed without the work being done: a new date is coming, not this one.
            visit.day = None
    upcoming = sorted((v for v in visits if is_upcoming(v)), key=lambda v: (v.day is None, v.day or date.max))
    history = sorted(
        (v for v in visits if not is_upcoming(v)),
        key=lambda v: v.day or timezone.localtime(v.reported_at).date(), reverse=True,
    )
    return upcoming, history


@login_required(login_url='customers:portal_login')
def portal_home(request):
    """A logged-in customer's start page: their next visit, how many
    requests are open, visits coming up and machines — each opening its own
    page — and what needs their attention (the bell's items, visits waiting
    for a rating). The lists themselves live on those pages, not here.
    """
    customer, setup = _portal_customer(request)
    if setup:
        return setup
    tickets = _portal_requests(customer)
    upcoming, history = _portal_visits(customer)
    # Needs your attention: the bell's items, then visits still to rate it doesn't already mention.
    in_bell = set(customer.notifications.filter(seen_at__isnull=True, task__isnull=False).values_list('task_id', flat=True))
    return render(request, 'customers/portal_home.html', {
        'customer': customer,
        'next_visit': next((v for v in upcoming if v.day), None),
        'open_count': sum(1 for ticket in tickets if ticket.is_open),
        'upcoming_count': len(upcoming),
        'machine_count': Asset.objects.filter(site__customer=customer).exclude(status=Asset.Status.RETIRED).count(),
        'to_rate': [visit for visit in history if visit.to_rate and visit.pk not in in_bell][:3],
    })


@login_required(login_url='customers:portal_login')
def portal_requests(request):
    """Every problem the customer has reported, newest first, with how far
    each has got."""
    customer, setup = _portal_customer(request)
    if setup:
        return setup
    tickets = _portal_requests(customer)
    # Two tabs: still open, and closed (done, resolved or closed).
    showing_closed = request.GET.get('show') == 'closed'
    return render(request, 'customers/portal_requests.html', {
        'tickets': [ticket for ticket in tickets if ticket.is_open != showing_closed],
        'showing_closed': showing_closed,
        'open_count': sum(1 for ticket in tickets if ticket.is_open),
        'closed_count': sum(1 for ticket in tickets if not ticket.is_open),
    })


@login_required(login_url='customers:portal_login')
def portal_visits(request):
    """The customer's visits, in two tabs like Requests: upcoming (soonest
    first, those without a date yet last) and past (finished or cancelled,
    newest first). Reports and ratings live on Reports."""
    customer, setup = _portal_customer(request)
    if setup:
        return setup
    upcoming, past = _portal_visits(customer)
    showing_past = request.GET.get('show') == 'past'
    return render(request, 'customers/portal_visits.html', {
        'visits': past if showing_past else upcoming, 'showing_past': showing_past,
        'upcoming_count': len(upcoming), 'past_count': len(past),
    })


@login_required(login_url='customers:portal_login')
def portal_reports(request):
    """Every visit report the customer has, newest first, each one click
    from its PDF."""
    customer, setup = _portal_customer(request)
    if setup:
        return setup
    upcoming, history = _portal_visits(customer)
    reports = sorted(
        (v for v in upcoming + history if v.has_report),
        key=lambda v: v.report.submitted_at or v.reported_at, reverse=True,
    )
    return render(request, 'customers/portal_reports.html', {'reports': reports})


@login_required(login_url='customers:portal_login')
def portal_visit_report_pdf(request, pk):
    """The visit report PDF for one of the customer's own visits, in their
    language (or ?lang=en / ?lang=ar). ?version=signed gives the copy they
    signed, when the report has changed since. Anyone else's visit, or one
    without a report yet, is simply not found.
    """
    from reports.pdf import render_visit_report_pdf, report_language

    customer = require_customer(request)
    if customer.must_change_password:
        return redirect('customers:portal_first_login')
    task = get_object_or_404(
        Task.objects.select_related('site__customer__country', 'task_type', 'report'), pk=pk, site__customer=customer,
    )
    report = getattr(task, 'report', None)
    if report is None:
        raise Http404
    language = request.GET.get('lang')
    if language not in dict(settings.LANGUAGES):
        language = report_language(customer)
    signed_version = request.GET.get('version') == 'signed' and bool(report.signed_content)
    response = HttpResponse(
        render_visit_report_pdf(report, language, signed_version=signed_version), content_type='application/pdf',
    )
    suffix = '-signed' if signed_version else ''
    response['Content-Disposition'] = f'inline; filename="{task.task_number}-visit-report{suffix}.pdf"'
    return response


@login_required(login_url='customers:portal_login')
def portal_settings(request):
    """The customer's own settings: contact details, addresses and language
    (CustomerSettingsForm), their portal color (PortalColorForm, its own
    section and button), and their password — the current one first,
    and an email saying it changed, the same as staff get. Their login
    email stays with the office (Customer accounts)."""
    customer, setup = _portal_customer(request)
    if setup:
        return setup
    details_form = CustomerSettingsForm(instance=customer)
    color_form = PortalColorForm(instance=customer)
    email_form = PortalEmailChangeForm(user=request.user)
    password_form = ChangePasswordForm(user=request.user)
    # Django focuses the current-password box, which would scroll past the details.
    password_form.fields['old_password'].widget.attrs.pop('autofocus', None)
    if request.method == 'POST' and request.POST.get('action') == 'details':
        details_form = CustomerSettingsForm(request.POST, instance=customer)
        if details_form.is_valid():
            details_form.save()
            messages.success(request, _('Your details have been saved.'))
            return redirect('customers:portal_settings')
    elif request.method == 'POST' and request.POST.get('action') == 'email':
        email_form = PortalEmailChangeForm(request.POST, user=request.user)
        if email_form.is_valid():
            new_email = email_form.cleaned_data['email']
            send_email_change_link(request, customer, new_email)
            messages.success(request, _(
                'We have sent a confirmation link to %(email)s. Your sign-in email changes once you open it.'
            ) % {'email': new_email})
            return redirect('customers:portal_settings')
    elif request.method == 'POST' and request.POST.get('action') == 'color':
        color_form = PortalColorForm(request.POST, instance=customer)
        if color_form.is_valid():
            color_form.save()
            messages.success(request, _('Your color has been saved.'))
            return redirect('customers:portal_settings')
    elif request.method == 'POST' and request.POST.get('action') == 'password':
        password_form = ChangePasswordForm(user=request.user, data=request.POST)
        if password_form.is_valid():
            password_form.save()
            update_session_auth_hash(request, request.user)
            send_password_changed_email(request.user)
            messages.success(request, _('Password changed.'))
            return redirect('customers:portal_settings')
    return render(request, 'customers/portal_settings.html', {
        'customer': customer, 'details_form': details_form, 'color_form': color_form,
        'email_form': email_form, 'password_form': password_form,
        # "Your details" in three groups, each under its own title.
        'details_groups': [
            (_('Contact'), ('contact_name', 'contact_phone', 'contact_email')),
            (_('Addresses'), ('billing_address', 'shipping_address')),
            (_('Language'), ('language',)),
        ],
    })


@login_required(login_url='customers:portal_login')
def portal_visit(request, pk):
    """One of the customer's visits: when and where, how far it has got,
    the request it came from, and — once the report is in — what the
    customer signed: each machine with what was found and done, the work
    done, the parts (no prices) and the recommendations, with the PDF and
    the signed copy. No hours: the same rule as the rest of the portal.
    A finished visit can be rated here (CustomerFeedback — the same record
    an emailed request uses, so one already sent is answered by this).
    """
    from reports.forms import CustomerFeedbackForm
    from reports.models import CustomerFeedback
    from reports.pdf import changed_after_signing
    from reports.phrases import Recommendation, WorkLine

    customer, setup = _portal_customer(request)
    if setup:
        return setup
    task = get_object_or_404(Task.objects.select_related('site', 'task_type'), pk=pk, site__customer=customer)
    notify.seen(customer, task=task)
    report = getattr(task, 'report', None)
    feedback = getattr(task, 'feedback', None)
    can_rate = report is not None and task.status in DONE_STATUSES and not (feedback and feedback.submitted_at)
    rating_form = CustomerFeedbackForm()
    if request.method == 'POST' and can_rate:
        rating_form = CustomerFeedbackForm(request.POST)
        if rating_form.is_valid():
            now = timezone.now()
            if feedback is None:
                feedback = CustomerFeedback(task=task, requested_at=now)
            feedback.rating = rating_form.cleaned_data['rating']
            feedback.comment = rating_form.cleaned_data['comment']
            feedback.submitted_at = now
            feedback.save()
            messages.success(request, _('Thank you for rating this visit.'))
            return redirect('customers:portal_visit', pk=task.pk)
    requests = list(
        customer.tickets.filter(Q(task=task) | Q(machines__task=task)).select_related('task').distinct()
        .order_by('submitted_at'),
    )
    for ticket in requests:
        ticket.progress = ticket_progress(ticket)
    context = {
        'visit': task, 'day': visit_day(task), 'progress': visit_progress(task), 'report': report,
        # Back to the list it's on: still to come, or history.
        'upcoming': is_upcoming(task),
        'feedback': feedback if feedback and feedback.submitted_at else None, 'can_rate': can_rate,
        'rating_form': rating_form,
        'booked': is_booked(visit_progress(task)),
        'requests': requests,
        'machines': [
            {
                'asset': line.asset, 'outcome': line.get_outcome_display(),
                'finding': line.get_finding_code_display(),
                'action': line.get_action_code_display() or line.action, 'note': line.note,
            }
            for line in task.task_assets.select_related('asset__brand').order_by('asset__model_name')
        ],
    }
    if report is not None:
        context.update({
            'work_lines': [WorkLine(code).label for code in report.work_lines if code in WorkLine.values],
            'recommendations': [
                Recommendation(code).label for code in report.recommendations if code in Recommendation.values
            ],
            'parts': report.parts_used.all(),
            'report_changed': changed_after_signing(report),
        })
    return render(request, 'customers/portal_visit.html', context)


@login_required(login_url='customers:portal_login')
def portal_machines(request):
    """Every machine at the customer's sites, by site: model, serial, when
    it was installed and last serviced. Retired ones are left out."""
    customer, setup = _portal_customer(request)
    if setup:
        return setup
    machines = list(
        Asset.objects.filter(site__customer=customer).exclude(status=Asset.Status.RETIRED)
        .select_related('site', 'brand').annotate(last_service=Max('task_assets__task__report__submitted_at'))
        .order_by('site__name', 'model_name'),
    )
    sites = {}
    for machine in machines:
        sites.setdefault(machine.site, []).append(machine)
    return render(request, 'customers/portal_machines.html', {'customer': customer, 'sites': sites.items()})


@login_required(login_url='customers:portal_login')
def portal_machine_add(request):
    """A customer adding one of their own machines (CustomerMachineForm). It
    joins their list at once, waiting for confirmation — a technician
    recording it at a visit, or the office checking it from the sticker
    photo, makes it one of ours (tasks.views.machine_detail)."""
    customer, setup = _portal_customer(request)
    if setup:
        return setup
    form = CustomerMachineForm(request.POST or None, request.FILES or None, customer=customer)
    if request.method == 'POST' and form.is_valid():
        machine = form.save(commit=False)
        machine.status = Asset.Status.PENDING
        machine.save()
        messages.success(request, _('Machine added. Our team will confirm it — you can already report a problem with it.'))
        return redirect('customers:portal_machine', pk=machine.pk)
    return render(request, 'customers/portal_machine_add.html', {'form': form})


@login_required(login_url='customers:portal_login')
def portal_machine(request, pk):
    """One of the customer's machines: its details and warranty, every visit
    that covered it (what was done, the report), and any request about it
    still open — with "Report a problem with this machine". Anyone else's
    machine is simply not found."""
    customer, setup = _portal_customer(request)
    if setup:
        return setup
    machine = get_object_or_404(Asset.objects.select_related('site', 'brand'), pk=pk, site__customer=customer)
    visits = []
    for line in machine.task_assets.select_related('task__task_type', 'task__report').order_by('-task__reported_at'):
        task = line.task
        visits.append({
            'task': task, 'outcome': line.get_outcome_display(), 'progress': visit_progress(task),
            'day': visit_day(task) or timezone.localtime(task.reported_at).date(),
            'has_report': getattr(task, 'report', None) is not None,
        })
    open_requests = [
        machine_line.ticket for machine_line in machine.ticket_machines.select_related('ticket__task')
        if machine_line.ticket.status in (CustomerTicket.Status.NEW, CustomerTicket.Status.CONVERTED)
    ]
    for ticket in open_requests:
        ticket.progress = ticket_progress(ticket)
    return render(request, 'customers/portal_machine.html', {
        'machine': machine, 'visits': visits, 'open_requests': open_requests,
    })


@login_required(login_url='customers:portal_login')
def portal_ticket_new(request):
    """A logged-in customer's new request — the customer and site are
    already known, so it's linked to this customer immediately, no
    matching needed. Where, then what they need (CustomerTicket.Topic).
    A machine problem has each machine as its own card
    (TicketMachineFormSet) with its own photos; the ticket's description
    and serial numbers are written from them, so everything that reads
    those keeps working. Any other topic is a message, which becomes the
    description, with its files on the ticket itself.
    """
    customer = require_customer(request)
    if customer.must_change_password:
        return redirect('customers:portal_first_login')

    if request.method == 'POST':
        form = CustomerPortalTicketForm(request.POST, request.FILES, customer=customer)
        machines = TicketMachineFormSet(request.POST, request.FILES, prefix='machines', form_kwargs={'customer': customer})
        # The machine cards only count for a machine problem.
        machines_ok = form.is_valid() and (not form.is_machine_problem or machines.is_valid())
        if machines_ok and form.is_machine_problem:
            site = form.cleaned_data['site']
            for machine_form in machines:
                asset = machine_form.cleaned_data.get('asset')
                if asset is not None and asset.site_id != site.pk:
                    machine_form.add_error('asset', _('This machine is registered at another site.'))
            machines_ok = machines.is_valid()
        if form.is_valid() and machines_ok:
            site = form.cleaned_data['site']
            lines = (
                [machine_form.cleaned_data for machine_form in machines if machine_form.has_changed()]
                if form.is_machine_problem else []
            )
            with transaction.atomic():
                ticket = form.save(commit=False)
                ticket.customer = customer
                ticket.site = site
                ticket.country = customer.country
                ticket.company_name = customer.name
                ticket.customer_code = customer.code
                ticket.site_description = site.name
                ticket.site_address = site.address
                ticket.submitted_at = timezone.now()
                if not form.is_machine_problem:
                    ticket.description = form.cleaned_data['message'].strip()
                save_new_ticket(ticket)
                for position, line in enumerate(lines):
                    machine = TicketMachine.objects.create(
                        ticket=ticket, asset=line['asset'], position=position,
                        model_name='' if line['asset'] else line['model_name'],
                        serial_no='' if line['asset'] else line['serial_no'],
                        code='' if line['asset'] else line.get('code', '').strip(),
                        serial_photo=None if line['asset'] else line['serial_photo'],
                        problem=line['problem'], description=line['description'],
                    )
                    for uploaded_file in line['files']:
                        CustomerTicketAttachment.objects.create(
                            ticket=ticket, machine=machine, file=uploaded_file, uploaded_at=timezone.now(),
                        )
                if form.is_machine_problem:
                    ticket.serial_numbers, ticket.description = ticket_machines_text(ticket.machines.all())
                    ticket.save(update_fields=['serial_numbers', 'description'])
                for uploaded_file in form.cleaned_data['attachments'] if not form.is_machine_problem else []:
                    CustomerTicketAttachment.objects.create(ticket=ticket, file=uploaded_file, uploaded_at=timezone.now())
                TicketNotification.objects.create(
                    ticket=ticket, kind=TicketNotification.Kind.NEW_TICKET, created_at=timezone.now(),
                )
            send_ticket_confirmation_to_customer(request, ticket)
            send_new_ticket_email_to_staff(request, ticket)
            messages.success(request, _('Ticket %(number)s submitted.') % {'number': ticket.ticket_number})
            return redirect('customers:portal_home')
    else:
        form = CustomerPortalTicketForm(customer=customer, initial={
            'contact_name': customer.contact_name, 'contact_phone': customer.contact_phone,
            'contact_email': customer.contact_email, 'shipping_address': customer.shipping_address,
        })
        machine_initial = []
        # From a machine's page ("Report a problem with this machine"): it and its site are chosen.
        chosen = Asset.objects.filter(
            pk=request.GET.get('machine', '') if request.GET.get('machine', '').isdigit() else None,
            site__customer=customer,
        ).exclude(status=Asset.Status.RETIRED).first()
        if chosen is not None:
            form.fields['site'].initial = chosen.site_id
            form.fields['topic'].initial = CustomerTicket.Topic.MACHINE_PROBLEM
            machine_initial = [{'which': 'list', 'asset': str(chosen.pk)}]
        machines = TicketMachineFormSet(prefix='machines', initial=machine_initial, form_kwargs={'customer': customer})

    return render(request, 'customers/portal_ticket_new.html', {'form': form, 'machines': machines})



def _account_redirect(request):
    """Back to the Customer accounts screen with the same filters."""
    query = request.POST.get('return_query', '')
    url = reverse('customers:customer_accounts')
    return redirect(f'{url}?{query}' if query else url)


INVITEABLE = (AccountStatus.NOT_INVITED, AccountStatus.INVITED, AccountStatus.EXPIRED)


@login_required
def customer_accounts(request):
    """Every customer's portal account in one place, for whoever manages
    them (customer_accounts — Admin, the Technical Support Manager and the
    Operations Manager by default): who has an account, who's been invited
    and hasn't finished setting up, who's locked out or disabled — and the
    actions to fix each case. Every country; a country filter narrows it.
    Each action is recorded in the Audit log.
    """
    require_permission(request, RolePermission.Permission.CUSTOMER_ACCOUNTS)
    locked = locked_usernames()

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'invite_selected':
            sent, skipped = 0, 0
            selected = Customer.objects.filter(pk__in=request.POST.getlist('customers'), is_active=True, is_internal=False)
            for customer in selected.select_related('user'):
                if account_status(customer, locked) in INVITEABLE and send_welcome(request, customer):
                    log_account_action(request, customer, 'Portal welcome email sent')
                    sent += 1
                else:
                    skipped += 1
            messages.success(request, _('Welcome emails sent: %(sent)s. Skipped: %(skipped)s.') % {
                'sent': sent, 'skipped': skipped,
            })
            return _account_redirect(request)

        if action == 'quick_invite':
            form = QuickInviteForm(request.POST)
            if form.is_valid():
                data = form.cleaned_data
                with transaction.atomic():
                    customer = Customer.objects.create(
                        name=data['name'].strip(), country=data['country'], segment=data['segment'],
                        contact_email=data['email'],
                    )
                    send_welcome(request, customer, email=data['email'])
                log_account_action(request, customer, 'Customer created and portal welcome email sent')
                messages.success(request, _('Customer %(name)s created and welcome email sent to %(email)s.') % {
                    'name': customer.name, 'email': data['email'],
                })
            else:
                for errors in form.errors.values():
                    messages.error(request, errors[0])
            return _account_redirect(request)

        customer = get_object_or_404(
            Customer.objects.select_related('user'), pk=request.POST.get('customer'), is_active=True,
        )
        user = customer.user
        status = account_status(customer, locked)
        if action == 'invite' and status in INVITEABLE:
            if send_welcome(request, customer):
                log_account_action(request, customer, 'Portal welcome email sent')
                messages.success(request, _('Welcome email sent to %(email)s.') % {'email': customer.user.email})
            else:
                messages.error(request, _('The email address %(email)s already belongs to another account.') % {
                    'email': customer.contact_email,
                })
        elif action == 'password_link' and status in (AccountStatus.ACTIVE, AccountStatus.LOCKED):
            send_password_link(request, customer)
            log_account_action(request, customer, 'Portal password link sent')
            messages.success(request, _('A password link has been sent to %(email)s.') % {'email': user.email})
        elif action == 'unlock' and user is not None:
            axes_reset(username=user.username)
            log_account_action(request, customer, 'Portal account unlocked')
            messages.success(request, _('%(name)s has been unlocked.') % {'name': customer.name})
        elif action in ('disable', 'enable') and user is not None:
            user.is_active = action == 'enable'
            user.save(update_fields=['is_active'])
            log_account_action(
                request, customer, 'Portal access enabled' if user.is_active else 'Portal access disabled',
            )
            messages.success(request, _('Portal access enabled.') if user.is_active else _('Portal access disabled.'))
        elif action == 'change_email' and user is not None:
            form = ChangeLoginEmailForm(request.POST, user=user)
            if form.is_valid():
                old_email = user.email
                user.username = user.email = form.cleaned_data['email']
                user.save(update_fields=['username', 'email'])
                log_account_action(request, customer, f'Portal login email changed from {old_email} to {user.email}')
                messages.success(request, _('Login email changed to %(email)s.') % {'email': user.email})
            else:
                messages.error(request, form.errors['email'][0])
        elif action == 'temporary_password' and user is not None and user.is_active:
            # Shown once, right here, for reading out over the phone — never
            # emailed, never stored anywhere readable.
            password = _generate_temporary_password()
            user.set_password(password)
            user.save(update_fields=['password'])
            customer.must_change_password = True
            customer.save(update_fields=['must_change_password'])
            log_account_action(request, customer, 'Portal temporary password set')
            return render(request, 'customers/customer_account_password.html', {
                'customer': customer, 'password': password, 'return_query': request.POST.get('return_query', ''),
            })
        return _account_redirect(request)

    # Our own account (the warehouses) never gets a portal login.
    customers = Customer.objects.filter(is_active=True, is_internal=False).select_related('user', 'country').order_by('name')
    country_id = request.GET.get('country', '')
    if country_id:
        customers = customers.filter(country_id=country_id)
    search = request.GET.get('q', '').strip()
    if search:
        customers = customers.filter(
            Q(name__icontains=search) | Q(contact_email__icontains=search) | Q(user__username__icontains=search),
        )
    now = timezone.now()
    rows = [{'customer': c, 'status': account_status(c, locked, now)} for c in customers]
    counts = {value: 0 for value in AccountStatus.values}
    for row in rows:
        counts[row['status']] += 1
    status = request.GET.get('status', '')
    if status in AccountStatus.values:
        rows = [row for row in rows if row['status'] == status]

    managed = next((row for row in rows if str(row['customer'].pk) == request.GET.get('manage')), None)
    query = request.GET.copy()
    query.pop('manage', None)
    return render(request, 'customers/customer_accounts.html', {
        'managed': managed, 'list_query': query.urlencode(),
        'page_obj': Paginator(rows, 50).get_page(request.GET.get('page')),
        'status_counts': [(value, label, counts[value]) for value, label in AccountStatus.choices],
        'active_count': counts[AccountStatus.ACTIVE], 'total': len(customers),
        'status': status, 'search': search, 'country_id': country_id,
        'countries': Country.objects.filter(is_active=True).order_by('name'),
        'return_query': request.GET.urlencode(), 'inviteable': INVITEABLE, 'link_days': LINK_VALID_DAYS,
        'quick_invite_form': QuickInviteForm(),
    })


def portal_email_confirm(request, token):
    """Where the link sent to a customer's new sign-in email lands: it
    switches their login to it, tells the old address, and records it in
    the Audit log. Works once, for EMAIL_CHANGE_VALID_DAYS."""
    user, new_email = email_change_for_token(token)
    if user is None:
        messages.error(request, _('This confirmation link has expired or was already used. Please request a new one.'))
    else:
        old_email = user.email
        user.username = user.email = new_email
        user.save(update_fields=['username', 'email'])
        log_account_action(
            request, user.customer, f'Portal sign-in email changed by the customer from {old_email} to {new_email}',
            user=user,
        )
        if old_email:
            send_email_changed_notice(user.customer, old_email)
        messages.success(request, _('Your sign-in email is now %(email)s.') % {'email': new_email})
    if request.user.is_authenticated and hasattr(request.user, 'customer'):
        return redirect('customers:portal_settings')
    return redirect('customers:portal_login')


def portal_account_link(request, token):
    """Where the welcome and password emails' secure link lands. A first
    visit completes the account (password plus company details); later it
    just sets a new password. Signs the customer in afterwards. An expired
    or already-used link explains itself and offers a fresh one.
    """
    user = user_for_link_token(token)
    if user is None:
        return render(request, 'customers/portal_link_invalid.html', status=400)
    if request.user.is_authenticated and request.user != user:
        logout(request)
    customer = user.customer
    first_time = customer.must_change_password

    if first_time:
        form = CustomerFirstLoginForm(request.POST or None, user=user, initial=_setup_initial(customer))
    else:
        form = NewPasswordForm(user=user, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        if first_time:
            _complete_setup(customer, user, form)
        else:
            form.save()
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        messages.success(request, _('Your account is ready. Welcome.') if first_time else _('Password changed.'))
        return redirect('customers:portal_home')

    return render(request, 'customers/portal_account_link.html', {'form': form, 'first_time': first_time})


def portal_password_help(request):
    """Forgot-your-password for customers. Always the same answer, whether
    or not the email belongs to a customer, so the form can't be used to
    find out who our customers are."""
    form = PortalPasswordHelpForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        customer = Customer.objects.filter(
            is_active=True, user__is_active=True, user__username__iexact=form.cleaned_data['email'],
        ).select_related('user').first()
        if customer is not None:
            if customer.must_change_password:
                send_welcome(request, customer)
            else:
                send_password_link(request, customer)
        return render(request, 'customers/portal_password_help.html', {'sent': True})
    return render(request, 'customers/portal_password_help.html', {'form': form})
