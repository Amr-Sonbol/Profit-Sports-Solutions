import csv
import io
from datetime import date, datetime

from axes.utils import reset as axes_reset
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Max, Q
from django.conf import settings
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.crypto import get_random_string
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from people.models import RolePermission, Technician
from people.permissions import (
    can_set_password,
    get_active_country, require_admin, require_manager, require_permission, scoped_or_404,
)
from reference.models import Country, TaskType
from tasks.forms import ChangePasswordForm, CustomerPortalTicketForm, NewPasswordForm, TicketMachineFormSet
from tasks.models import CustomerTicket, CustomerTicketAttachment, Task, TicketMachine, TicketNotification
from tasks.views import (
    save_new_ticket, send_new_ticket_email_to_staff, send_password_changed_email, send_ticket_confirmation_to_customer,
    ticket_machines_text,
)

from .accounts import (
    LINK_VALID_DAYS, AccountStatus, account_status, add_person, customer_status, customer_statuses,
    email_change_for_token, invite_customer, locked_usernames, log_account_action, send_email_change_link,
    send_email_changed_notice, send_password_link, send_welcome, user_for_link_token, welcome_link,
)
from .forms import (
    AddPortalUserForm, ChangeLoginEmailForm, CustomerEditForm, NewCustomerForm, CustomerFirstLoginForm,
    CustomerTermsForm, PortalBranchRequestForm, SiteTermsForm,
    CustomerImportForm, CustomerMachineForm, CustomerSettingsForm, MyAccountForm, DeactivateCustomerForm, PortalColorForm,
    SignInEmailChangeForm, PortalLoginForm, PortalPasswordHelpForm,
    SiteCreateForm, SiteEditForm,
)
from spots.throttle import link_email_allowed

from .models import Asset, Customer, PortalUser, Site, portal_customer
from . import notify
from .progress import (
    DONE_STATUSES, is_booked, is_upcoming, progress_steps, ticket_progress, visit_day, visit_progress, with_today_taps,
)

# Only the name is required: a file of just customer_name and
# customer_contact_email is enough to invite everyone, and each customer
# completes the rest themselves at first sign-in.
CUSTOMER_IMPORT_REQUIRED_COLUMNS = ['customer_name']
CUSTOMER_IMPORT_COLUMNS = CUSTOMER_IMPORT_REQUIRED_COLUMNS + [
    'customer_contact_email', 'segment', 'customer_contact_name', 'customer_contact_phone',
    'site_name', 'site_address', 'site_contact_name', 'site_contact_phone', 'site_contact_email', 'access_notes',
    'warranty_start',
]


def _import_date(value):
    """A date as spreadsheets write it — 2026-03-15, 15/03/2026 or 15-03-2026;
    None when empty; ValueError when it can't be read."""
    value = (value or '').strip()
    if not value:
        return None
    for shape in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y'):
        try:
            return datetime.strptime(value, shape).date()
        except ValueError:
            continue
    raise ValueError(value)

# Avoids characters easy to mis-type or mis-read out loud/over chat: 0/O, 1/l/I.
TEMPORARY_PASSWORD_ALPHABET = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789'


def _generate_temporary_password():
    return get_random_string(12, allowed_chars=TEMPORARY_PASSWORD_ALPHABET)


def _manages_logins(technician):
    """Whoever manages customers' portal logins (customer_accounts — Admin, the
    Operations and the Technical Support Manager by default): they also create
    customers and add branches."""
    return RolePermission.objects.filter(
        role=technician.permission_role, permission=RolePermission.Permission.CUSTOMER_ACCOUNTS, allowed=True,
    ).exists()


@login_required
def customer_list(request):
    """The one list of customers: the active country's, or — for whoever
    reaches every country — any one country or all of them (?country=all).
    Deactivated ones on their own tab. Whoever manages portal logins also
    sees each one's login (status, last sign-in), filters by it (?portal=)
    and sends welcome emails to several at once.
    """
    technician = require_permission(request, RolePermission.Permission.VIEW_CUSTOMERS)
    manages_logins = _manages_logins(technician)

    show_inactive = request.GET.get('status') == 'inactive'
    search = request.GET.get('q', '').strip()
    country_id = request.GET.get('country', '')
    customers = Customer.objects.filter(is_active=not show_inactive)
    if technician.reaches_all_countries and country_id == 'all':
        pass
    elif technician.reaches_all_countries and country_id.isdigit():
        customers = customers.filter(country_id=country_id)
    else:
        country_id = ''
        customers = customers.filter(country=get_active_country(request))
    if search:
        customers = customers.filter(
            Q(name__matches=search) | Q(contact_email__icontains=search) | Q(contact_phone__phone=search)
            | Q(portal_users__user__username__icontains=search),
        ).distinct()
    customers = customers.select_related('country').prefetch_related('sites', 'portal_users__user').order_by('name')

    rows = [{'customer': customer} for customer in customers]
    portal = ''
    status_counts = []
    if manages_logins:
        locked, now = locked_usernames(), timezone.now()
        counts = {value: 0 for value in AccountStatus.values}
        for row in rows:
            # Our own account (the warehouses) never gets a portal login.
            customer = row['customer']
            if not customer.is_internal:
                # One status for the row (what most needs doing first); a tab finds a customer by any of its people's.
                row['statuses'] = customer_statuses(customer, locked, now)
                row['portal'] = customer_status(row['statuses'])
                row['people'] = len(customer.portal_users.all())
                row['last_login'] = max((person.user.last_login for person in customer.portal_users.all()
                                         if person.user.last_login), default=None)
                for status in row['statuses']:
                    counts[status] += 1
        status_counts = [(value, label, counts[value]) for value, label in AccountStatus.choices]
        portal = request.GET.get('portal', '')
        if portal in AccountStatus.values:
            rows = [row for row in rows if portal in row.get('statuses', ())]
        else:
            portal = ''

    # 50 to a page: every country's runs to thousands as the company grows.
    page_obj = Paginator(rows, 50).get_page(request.GET.get('page'))
    query = request.GET.copy()
    query.pop('page', None)
    return render(request, 'customers/customer_list.html', {
        'page_obj': page_obj, 'search': search, 'show_inactive': show_inactive,
        'country_id': country_id, 'manages_logins': manages_logins, 'can_create': manages_logins,
        'all_countries': technician.reaches_all_countries,
        'countries': Country.objects.filter(is_active=True).order_by('name') if technician.reaches_all_countries else [],
        'portal': portal, 'status_counts': status_counts, 'inviteable': INVITEABLE, 'link_days': LINK_VALID_DAYS,
        'page_query': query.urlencode(), 'return_query': request.GET.urlencode(),
    })


@login_required
def customer_create(request):
    """The one way to create a customer (NewCustomerForm): the customer, its
    first branch and contact, and the portal invitation, ticked by default.
    For whoever manages customer accounts (customer_accounts — Admin, the
    Operations and the Technical Support Manager by default): creating a
    customer is also where its country is set, so not every manage_customers
    role (supervisors have it).

    ?next=ticket — from "New ticket", for a caller who isn't a customer yet:
    the country is the ticket's (the active one), and saving goes back to
    the ticket with the new branch chosen.
    """
    require_permission(request, RolePermission.Permission.CUSTOMER_ACCOUNTS)
    next_page = request.GET.get('next', '')
    country = get_active_country(request) if next_page == 'ticket' else None
    form = NewCustomerForm(request.POST or None, country=country)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            customer, site = form.save()
        invited = form.cleaned_data['invite'] and invite_customer(request, customer)
        log_account_action(request, customer, 'Customer created and portal welcome email sent' if invited
                           else 'Customer created')
        if invited:
            messages.success(request, _('Customer %(name)s created and welcome email sent to %(email)s.') % {
                'name': customer.name, 'email': customer.contact_email,
            })
        else:
            messages.success(request, _('Customer added.'))
        if next_page == 'ticket':
            return redirect(f"{reverse('tasks:ticket_open')}?site={site.pk}")
        return redirect('customers:customer_detail', pk=customer.pk)

    return render(request, 'customers/customer_create.html', {'form': form, 'next_page': next_page, 'country': country})


@login_required
def customer_detail(request, pk):
    """The customer's sites, and — for whoever manages portal logins — a way
    to add another one and their Portal login section (portal_login_action).
    Whoever reaches every country opens any customer, whatever the active
    one (the Customers list's All countries); everyone else stays scoped to it.
    """
    requesting_technician = require_permission(request, RolePermission.Permission.VIEW_CUSTOMERS)
    is_admin = requesting_technician.role == Technician.Role.ADMIN
    # Adding a branch, and the Portal login section: whoever manages portal logins.
    can_add_site = _manages_logins(requesting_technician)
    customer = scoped_or_404(Customer.objects, pk, requesting_technician, get_active_country(request), 'country')

    # Commercial terms: managers and admins only; every change to the Audit log.
    can_see_terms = requesting_technician.is_manager_tier
    terms_form = CustomerTermsForm(instance=customer) if can_see_terms else None
    if request.method == 'POST' and request.POST.get('action') == 'terms' and can_see_terms:
        terms_form = CustomerTermsForm(request.POST, instance=customer)
        if terms_form.is_valid():
            _save_terms_logged(request, terms_form, customer)
            messages.success(request, _('Commercial terms saved.'))
            return redirect(f"{reverse('customers:customer_detail', args=[customer.pk])}?tab=sites#terms")

    # From a customer's new-branch request (ticket_review's "Add this branch"): the form filled in.
    branch_initial = {
        field: request.GET[param] for param, field in (
            ('branch_name', 'name'), ('branch_address', 'address'),
            ('branch_contact', 'contact_name'), ('branch_phone', 'contact_phone'),
        ) if request.GET.get(param)
    }
    form = SiteCreateForm(customer=customer, initial=branch_initial) if can_add_site else None
    # The new-branch request it comes from, if any: still waiting for its branch.
    request_id = request.POST.get('from_request') or request.GET.get('from_request') or ''
    from_request = CustomerTicket.objects.filter(
        pk=request_id, customer=customer, about=CustomerTicket.About.NEW_BRANCH, site__isnull=True,
    ).first() if request_id.isdigit() else None
    if request.method == 'POST' and can_add_site and request.POST.get('action') != 'terms':
        form = SiteCreateForm(request.POST, customer=customer)
        if form.is_valid():
            site = form.save(commit=False)
            site.customer = customer
            site.save()
            if from_request is not None:
                # The request now has its branch: a task made from it (its installation, say) starts there.
                from_request.site = site
                from_request.save(update_fields=['site'])
                messages.success(request, _('Branch added and linked to request %(number)s.') % {
                    'number': from_request.ticket_number,
                })
                return redirect('tasks:ticket_review', pk=from_request.pk)
            messages.success(request, _('Site added.'))
            return redirect('customers:customer_detail', pk=customer.pk)

    context = {
        'customer': customer, 'sites': customer.sites.all(), 'form': form, 'is_admin': is_admin,
        'can_add_site': can_add_site,
        # Editing the customer and its branches: the edit switch (a supervisor only sees them).
        'can_edit': requesting_technician.has_permission(RolePermission.Permission.MANAGE_CUSTOMERS),
        # Its people who sign in to the portal, and what can be done to each (portal_login_action);
        # never our own warehouses.
        'portal_section': can_add_site and not customer.is_internal,
        'portal_people': _portal_people(customer) if can_add_site and not customer.is_internal else [],
        'link_days': LINK_VALID_DAYS, 'add_person_form': AddPortalUserForm(),
        'can_see_terms': can_see_terms, 'terms_form': terms_form, 'from_request': from_request,
        'today': timezone.localdate(),
        **_customer_overview(request, customer),
    }
    return render(request, 'customers/customer_detail.html', context)


CUSTOMER_TABS = ('sites', 'requests', 'tasks', 'machines', 'reports')
CUSTOMER_TAB_ROWS = 25


@login_required
def view_as_start(request, pk):
    """Admin only: see the portal exactly as this customer does, to check a
    complaint (spots.middleware.ViewAsCustomerMiddleware) — view only, their
    password untouched, nothing sent to them; recorded in the Audit log."""
    from spots.middleware import VIEW_AS_SESSION_KEY

    technician = require_permission(request, RolePermission.Permission.VIEW_CUSTOMERS)
    if technician.role != Technician.Role.ADMIN or request.method != 'POST':
        raise PermissionDenied
    customer = get_object_or_404(Customer, pk=pk)
    if not customer.portal_users.filter(user__is_active=True).exists():
        messages.error(request, _('This customer has no active portal login to view.'))
        return redirect('customers:customer_detail', pk=customer.pk)
    log_account_action(request, customer, f'Admin viewed the portal as this customer ({request.user.get_username()})')
    request.session[VIEW_AS_SESSION_KEY] = customer.pk
    return redirect('customers:portal_home')


@login_required
def view_as_stop(request):
    """Back from viewing as a customer (or a staff member) to the admin's own
    pages, on that customer's (or person's) page."""
    from spots.middleware import VIEW_AS_SESSION_KEY, VIEW_AS_USER_SESSION_KEY

    customer_id = request.session.pop(VIEW_AS_SESSION_KEY, None)
    user_id = request.session.pop(VIEW_AS_USER_SESSION_KEY, None)
    if customer_id:
        return redirect('customers:customer_detail', pk=customer_id)
    technician = Technician.objects.filter(user_id=user_id).first() if user_id else None
    if technician is not None:
        return redirect('tasks:technician_detail', pk=technician.pk)
    return redirect('home')


def _customer_overview(request, customer):
    """Everything about one customer for a manager or admin, on its page:
    headline numbers, then one tab at a time (?tab=) — its sites, requests,
    tasks, machines, or visit reports with their ratings — each the latest
    CUSTOMER_TAB_ROWS, the full list a link away."""
    from reports.models import CustomerFeedback, WorkReport

    requests = CustomerTicket.objects.filter(customer=customer, kind=CustomerTicket.Kind.CUSTOMER)
    tasks = Task.objects.filter(site__customer=customer)
    machines = Asset.objects.filter(site__customer=customer).exclude(status=Asset.Status.RETIRED)
    ratings = CustomerFeedback.objects.filter(task__site__customer=customer, rating__isnull=False)
    open_task_statuses = [
        Task.Status.NEW, Task.Status.ASSIGNED, Task.Status.ACCEPTED, Task.Status.IN_PROGRESS, Task.Status.BLOCKED,
    ]
    rating = ratings.aggregate(average=Avg('rating'), count=Count('pk'))
    tab = request.GET.get('tab') if request.GET.get('tab') in CUSTOMER_TABS else 'sites'
    overview = {
        'tab': tab,
        'open_requests': requests.filter(status=CustomerTicket.Status.NEW).count(),
        'open_tasks': tasks.filter(status__in=open_task_statuses).count(),
        'machine_count': machines.count(),
        'rating_average': round(rating['average'], 1) if rating['average'] is not None else None,
        'rating_count': rating['count'],
        'counts': {
            'sites': customer.sites.count(), 'requests': requests.count(), 'tasks': tasks.count(),
            'machines': machines.count(), 'reports': WorkReport.objects.filter(task__site__customer=customer).count(),
        },
    }
    if tab == 'requests':
        overview['rows'] = requests.select_related('site', 'task').order_by('-submitted_at')[:CUSTOMER_TAB_ROWS]
    elif tab == 'tasks':
        overview['rows'] = tasks.select_related('site', 'task_type').order_by('-reported_at')[:CUSTOMER_TAB_ROWS]
    elif tab == 'machines':
        overview['rows'] = machines.select_related('site', 'brand').order_by(
            'site__name', 'model_name',
        )[:CUSTOMER_TAB_ROWS]
    elif tab == 'reports':
        overview['rows'] = WorkReport.objects.filter(task__site__customer=customer).select_related(
            'task__site', 'task__task_type', 'task__feedback',
        ).order_by('-submitted_at')[:CUSTOMER_TAB_ROWS]
    return overview


@login_required
def customer_edit(request, pk):
    """Everything about an existing customer in one place — their own
    details, and deactivating them. Their portal logins are on their page's
    Portal login section. A manager or admin reaches any customer regardless
    of active country, same reasoning as customer_detail.
    """
    requesting_technician = require_permission(request, RolePermission.Permission.MANAGE_CUSTOMERS)
    is_manager = requesting_technician.is_manager_tier
    customer = scoped_or_404(Customer.objects, pk, requesting_technician, get_active_country(request), 'country')

    form = CustomerEditForm(instance=customer)
    deactivate_form = DeactivateCustomerForm()

    if request.method == 'POST' and request.POST.get('action') == 'deactivate' and is_manager:
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
            messages.success(request, _('Customer updated.'))
            return redirect('customers:customer_detail', pk=customer.pk)

    context = {
        'customer': customer, 'form': form, 'deactivate_form': deactivate_form, 'is_manager': is_manager,
    }
    return render(request, 'customers/customer_edit.html', context)


@login_required
def site_edit(request, pk):
    require_permission(request, RolePermission.Permission.MANAGE_CUSTOMERS)
    site = get_object_or_404(Site, pk=pk, customer__country=get_active_country(request))

    technician = request.user.technician
    terms_form = SiteTermsForm(instance=site) if technician.is_manager_tier else None
    form = SiteEditForm(instance=site)
    if request.method == 'POST' and request.POST.get('action') == 'terms' and terms_form is not None:
        terms_form = SiteTermsForm(request.POST, instance=site)
        if terms_form.is_valid():
            _save_terms_logged(request, terms_form, site.customer, branch=site)
            messages.success(request, _('This branch\'s terms saved.'))
            return redirect('customers:site_edit', pk=site.pk)
    elif request.method == 'POST':
        form = SiteEditForm(request.POST, instance=site)
        if form.is_valid():
            form.save()
            messages.success(request, _('Site updated.'))
            return redirect('customers:customer_detail', pk=site.customer_id)

    return render(request, 'customers/site_edit.html', {'site': site, 'form': form, 'terms_form': terms_form})


def _save_terms_logged(request, form, customer, branch=None):
    """Save commercial terms and record each changed one in the Audit log,
    from → to (money is involved)."""
    changes = [
        f'{form.fields[name].label}: {form.initial.get(name) or "—"} → {form.cleaned_data[name] or "—"}'
        for name in form.changed_data
    ]
    form.save()
    if changes:
        where = f' (branch {branch.name})' if branch else ''
        log_account_action(request, customer, f'Commercial terms changed{where}: ' + '; '.join(changes))


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

        try:
            warranty_start = _import_date(row.get('warranty_start'))
        except ValueError:
            results.append({
                **result, 'outcome': 'error',
                'detail': _(
                    'warranty_start "%(value)s" is not a date. Please write it as 2026-03-15 or 15/03/2026.'
                ) % {'value': row.get('warranty_start')},
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

                existing = Site.objects.filter(customer=customer, name__iexact=site_name).first()
                if existing is not None:
                    if warranty_start and not existing.warranty_start:
                        existing.warranty_start, existing.warranty_until = warranty_start, None
                        existing.save()
                        results.append({
                            **result, 'outcome': 'created', 'detail': _('Warranty date added to existing site'),
                        })
                    else:
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
                    warranty_start=warranty_start,
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
    writer.writerow(['Gold Gym', 'info@goldgym.example', '', '', '', '', '', '', '', '', '', ''])
    # Or with sites: one row per branch, sharing the customer name; warranty_start (optional) is the
    # installation date — the warranty runs two years from it.
    writer.writerow([
        'Fitness First', 'ali@example.com', 'gym', 'Ali Manager', '0501234567',
        'Marina Branch', 'Dubai Marina, near the mall', '', '', '', 'Gate code 1234', '2026-03-15',
    ])
    writer.writerow(['Fitness First', '', 'gym', '', '', 'JBR Branch', 'JBR, near the beach', '', '', '', '', ''])
    return response


def require_customer(request):
    """Restrict a view to a customer's own portal login — the customer-
    side counterpart to people.permissions.require_technician."""
    customer = portal_customer(request.user)
    if customer is None:
        raise PermissionDenied
    return customer


def _needs_setup(user):
    """Still to set their own password (invited, or given a temporary one)."""
    return user.portal_user.must_change_password


def _portal_landing(user):
    """Where a customer login lands right after signing in — the one-time
    setup screen if they're still on a temporary password, their account
    home otherwise.
    """
    if _needs_setup(user):
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
        return _portal_landing(request.user) if hasattr(request.user, 'portal_user') else redirect('home')

    next_url = request.POST.get('next') or request.GET.get('next') or ''

    form = PortalLoginForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        if hasattr(user, 'portal_user'):
            login(request, user)
            # "Keep me signed in" (ticked by default): the usual two weeks; unticked — a shared
            # computer — the sign-in ends when the browser closes.
            if not request.POST.get('remember'):
                request.session.set_expiry(0)
            if _needs_setup(user):
                return redirect('customers:portal_first_login')
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
            return redirect('customers:portal_home')
        form.add_error(None, form.error_messages['invalid_login'])

    return render(request, 'customers/portal_login.html', {
        'form': form, 'next': next_url,
        'remember': request.method != 'POST' or bool(request.POST.get('remember')),
    })


SETUP_FIELDS = ['contact_name', 'contact_phone', 'contact_email', 'billing_address', 'shipping_address']


def _setup_initial(customer):
    return {field: getattr(customer, field) for field in SETUP_FIELDS}


def _complete_setup(customer, user, form):
    """Save what the person filled in at first sign-in — the company's
    details, prefilled with what's on file — and their new password; the
    portal opens normally from then on. `user` is the very object the
    session holds, so its new password hash is the one the session is
    re-keyed to."""
    for field in SETUP_FIELDS:
        setattr(customer, field, form.cleaned_data[field])
    customer.save(update_fields=SETUP_FIELDS)
    user.portal_user.must_change_password = False
    user.portal_user.save(update_fields=['must_change_password'])
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
    if not _needs_setup(request.user):
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
    return customer, redirect('customers:portal_first_login') if _needs_setup(request.user) else None


def _portal_requests(customer):
    """The customer's requests (tickets), newest first, each with its
    progress in their words (customers.progress), whether it's open, and
    the visit whose report it has (`report_visit`), if any."""
    tickets = list(customer.tickets.select_related('task__report').order_by('-submitted_at'))
    for ticket in tickets:
        ticket.report_visit = ticket.task if ticket.task_id and getattr(ticket.task, 'report', None) else None
        ticket.progress = ticket_progress(ticket)
        ticket.steps = progress_steps(ticket.progress) if ticket.heads_for_a_visit else None
        # A cancelled visit doesn't settle the request: it stays open, as it does for the team.
        ticket.is_open = ticket.status == CustomerTicket.Status.NEW or (
            ticket.status == CustomerTicket.Status.CONVERTED and ticket.task_id
            and (is_upcoming(ticket.task) or ticket.task.status == Task.Status.CANCELLED)
        )
    return tickets


def _portal_visits(customer):
    """The customer's visits as (coming up, history): coming up soonest
    first and those without a date yet last; history newest first, each
    with its report PDF (the customer-facing document emailed to them, no
    hours or prices) and whether it's waiting for their rating."""
    from reports.pdf import changed_after_signing

    # A part shipment is a parcel, not a visit: it shows on its request instead.
    visits = with_today_taps(Task.objects.filter(site__customer=customer).exclude(
        task_type__category=TaskType.Category.PART_SHIPMENT,
    ).select_related('site__customer__country', 'task_type', 'report', 'feedback'))
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
    if _needs_setup(request.user):
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
    """Settings, in two parts. My account — this person's own: name and
    language (MyAccountForm), sign-in email (changed once they open a link
    sent to the new address) and password (the current one first, and an
    email saying it changed). Company — shared by everyone at it: contact
    details and addresses (CustomerSettingsForm; each change in the Audit
    log), branches, the portal color, and who has access (read-only: our
    staff add and remove people)."""
    customer, setup = _portal_customer(request)
    if setup:
        return setup
    person = request.user.portal_user
    account_form = MyAccountForm(person=person)
    details_form = CustomerSettingsForm(instance=customer)
    color_form = PortalColorForm(instance=customer)
    email_form = SignInEmailChangeForm(user=request.user)
    password_form = ChangePasswordForm(user=request.user)
    # Django focuses the current-password box, which would scroll past the details.
    password_form.fields['old_password'].widget.attrs.pop('autofocus', None)
    if request.method == 'POST' and request.POST.get('action') == 'account':
        account_form = MyAccountForm(request.POST, person=person)
        if account_form.is_valid():
            account_form.save()
            messages.success(request, _('Your details have been saved.'))
            return redirect('customers:portal_settings')
    elif request.method == 'POST' and request.POST.get('action') == 'details':
        details_form = CustomerSettingsForm(request.POST, instance=customer)
        if details_form.is_valid():
            details_form.save()
            if details_form.changed_data:
                with translation.override('en'):  # the Audit log is in English
                    changed = ', '.join(str(CustomerSettingsForm.Meta.labels[name]) for name in details_form.changed_data)
                log_account_action(request, customer, f'Company details changed in the portal: {changed}')
            messages.success(request, _('Company details saved.'))
            return redirect('customers:portal_settings')
    elif request.method == 'POST' and request.POST.get('action') == 'email':
        email_form = SignInEmailChangeForm(request.POST, user=request.user)
        if email_form.is_valid():
            new_email = email_form.cleaned_data['email']
            send_email_change_link(request, request.user.portal_user, new_email)
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
        'customer': customer, 'account_form': account_form, 'details_form': details_form, 'color_form': color_form,
        # Who has access: read-only here (our staff add and remove people).
        'people': customer.portal_users.select_related('user').order_by('user__first_name', 'user__email'),
        'email_form': email_form, 'password_form': password_form,
        # Their branches, and new ones they've asked us to add that we haven't yet.
        'branches': customer.sites.order_by('name'),
        'branch_requests': customer.tickets.filter(
            about=CustomerTicket.About.NEW_BRANCH, status=CustomerTicket.Status.NEW,
        ).order_by('-submitted_at'),
        # The company's details in two groups, each under its own title.
        'details_groups': [
            (_('Contact'), ('contact_name', 'contact_phone', 'contact_email')),
            (_('Addresses'), ('billing_address', 'shipping_address')),
            (_('Notifications'), ('notify_on_the_way',)),
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
    if not request.viewing_as:  # an admin viewing as them doesn't read their bell for them
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
def portal_branch_request(request):
    """A customer asking us to add a branch they've opened: it arrives as a
    request ("A new branch") for the team, like any other — nothing is
    created from it. An admin adds the branch from that request
    (tasks.views.ticket_review's "Add this branch")."""
    customer = require_customer(request)
    if _needs_setup(request.user):
        return redirect('customers:portal_first_login')
    form = PortalBranchRequestForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        lines = [
            _('Branch name: %(name)s') % {'name': data['name']},
            _('Address: %(address)s') % {'address': data['address']},
        ]
        if data['contact_name'] or data['contact_phone'] or data['contact_email']:
            lines.append(_('Contact: %(name)s %(phone)s %(email)s') % {
                'name': data['contact_name'], 'phone': data['contact_phone'], 'email': data['contact_email'],
            })
        if data['opening_date']:
            lines.append(_('Opening date: %(date)s') % {'date': data['opening_date'].isoformat()})
        if data['message']:
            lines.append(data['message'])
        with transaction.atomic():
            ticket = CustomerTicket(
                customer=customer, submitted_by=request.user, country=customer.country, company_name=customer.name,
                customer_code=customer.code, site_description=data['name'], site_address=data['address'],
                contact_name=data['contact_name'] or customer.contact_name or customer.name,
                contact_phone=data['contact_phone'] or customer.contact_phone,
                contact_email=customer.contact_email,
                about=CustomerTicket.About.NEW_BRANCH, need=CustomerTicket.Need.ASSISTANCE,
                subject=data['name'], description='\n'.join(lines), submitted_at=timezone.now(),
            )
            save_new_ticket(ticket)
            TicketNotification.objects.create(
                ticket=ticket, kind=TicketNotification.Kind.NEW_TICKET, created_at=timezone.now(),
            )
        send_ticket_confirmation_to_customer(request, ticket)
        send_new_ticket_email_to_staff(request, ticket)
        messages.success(request, _('Thank you. We have received your new branch and will add it to your account.'))
        return redirect('customers:portal_requests')
    return render(request, 'customers/portal_branch_request.html', {'form': form})


@login_required(login_url='customers:portal_login')
def portal_ticket_new(request):
    """A logged-in customer's new request — the customer and site are
    already known, so it's linked to this customer immediately, no
    matching needed. Where, then what it's about and what they need
    (CustomerTicket.About, CustomerTicket.Need).
    A machine problem has each machine as its own card
    (TicketMachineFormSet) with its own photos; the ticket's description
    and serial numbers are written from them, so everything that reads
    those keeps working. Anything else is a message, which becomes the
    description, with its files on the ticket itself.
    """
    customer = require_customer(request)
    if _needs_setup(request.user):
        return redirect('customers:portal_first_login')

    if request.method == 'POST':
        form = CustomerPortalTicketForm(request.POST, request.FILES, customer=customer)
        machines = TicketMachineFormSet(
            request.POST, request.FILES, prefix='machines',
            form_kwargs={'customer': customer, 'for_parts': request.POST.get('need') == CustomerTicket.Need.PARTS},
        )
        # The machine cards only count for a machine problem or spare parts.
        machines_ok = form.is_valid() and (not form.uses_machines or machines.is_valid())
        if machines_ok and form.uses_machines:
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
                if form.uses_machines else []
            )
            with transaction.atomic():
                ticket = form.save(commit=False)
                ticket.customer = customer
                ticket.submitted_by = request.user
                ticket.site = site
                ticket.country = customer.country
                ticket.company_name = customer.name
                ticket.customer_code = customer.code
                ticket.site_description = site.name
                ticket.site_address = site.address
                ticket.submitted_at = timezone.now()
                if not form.uses_machines:
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
                if form.uses_machines:
                    ticket.serial_numbers, ticket.description = ticket_machines_text(ticket.machines.all())
                    ticket.save(update_fields=['serial_numbers', 'description'])
                for uploaded_file in form.cleaned_data['attachments'] if not form.uses_machines else []:
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
            # The person sending it: they hear back about it.
            'contact_name': request.user.first_name or customer.contact_name, 'contact_phone': customer.contact_phone,
            'contact_email': request.user.email or customer.contact_email, 'shipping_address': customer.shipping_address,
        })
        machine_initial = []
        # From a machine's page ("Report a problem with this machine"): it and its site are chosen.
        chosen = Asset.objects.filter(
            pk=request.GET.get('machine', '') if request.GET.get('machine', '').isdigit() else None,
            site__customer=customer,
        ).exclude(status=Asset.Status.RETIRED).first()
        if chosen is not None:
            form.fields['site'].initial = chosen.site_id
            form.fields['about'].initial = CustomerTicket.About.MACHINES
            form.fields['need'].initial = CustomerTicket.Need.REPAIR
            machine_initial = [{'which': 'list', 'asset': str(chosen.pk)}]
        machines = TicketMachineFormSet(prefix='machines', initial=machine_initial, form_kwargs={'customer': customer})

    return render(request, 'customers/portal_ticket_new.html', {'form': form, 'machines': machines})



def _account_redirect(request, customer=None):
    """Back where the action came from: the customer's page (its Portal
    login section), or the Customers list with the same filters."""
    if customer is not None and request.POST.get('from') != 'list':
        return redirect(f"{reverse('customers:customer_detail', args=[customer.pk])}#portal")
    query = request.POST.get('return_query', '')
    url = reverse('customers:customer_list')
    return redirect(f'{url}?{query}' if query else url)


INVITEABLE = (AccountStatus.NOT_INVITED, AccountStatus.INVITED, AccountStatus.EXPIRED)


def _portal_people(customer):
    """A customer's people who sign in to the portal, each with their login's status."""
    locked, now = locked_usernames(), timezone.now()
    return [{'person': person, 'status': account_status(person, locked, now)}
            for person in customer.portal_users.select_related('user')]


@login_required
def portal_login_action(request):
    """What's done to a customer's portal logins, from its Portal login
    section on the customer's page: invite the contact as the first person
    (`invite`, `customer`), add a person (`add_person`, `customer`, `name`,
    `email`), or act on one (`person` — resend the welcome email or copy its
    link, send a password link, unlock, disable or enable, change the login
    email, set a temporary password, remove). From the Customers list,
    welcome emails to several customers at once (`invite_selected`,
    `customers`). For whoever manages portal logins (customer_accounts); each
    action is recorded in the Audit log, on the customer.
    """
    require_permission(request, RolePermission.Permission.CUSTOMER_ACCOUNTS)
    if request.method != 'POST':
        return redirect('customers:customer_list')
    locked = locked_usernames()
    action = request.POST.get('action')
    taken = _('The email address %(email)s already belongs to another account.')

    if action == 'invite_selected':
        # No one yet: their contact is invited. Invited but not set up: sent again.
        sent, skipped = 0, 0
        selected = Customer.objects.filter(pk__in=request.POST.getlist('customers'), is_active=True, is_internal=False)
        for customer in selected.prefetch_related('portal_users__user'):
            people = [person for person in customer.portal_users.all()
                      if account_status(person, locked) in (AccountStatus.INVITED, AccountStatus.EXPIRED)]
            if not customer.portal_users.all() and invite_customer(request, customer):
                log_account_action(request, customer, 'Portal welcome email sent')
                sent += 1
            elif people:
                for person in people:
                    send_welcome(request, person)
                    log_account_action(request, customer, f'Portal welcome email sent to {person.user.email}')
                sent += 1
            else:
                skipped += 1
        messages.success(request, _('Welcome emails sent: %(sent)s. Skipped: %(skipped)s.') % {
            'sent': sent, 'skipped': skipped,
        })
        return _account_redirect(request)

    if action in ('invite', 'add_person'):
        customer = get_object_or_404(Customer, pk=request.POST.get('customer'), is_active=True, is_internal=False)
        if action == 'invite':
            if invite_customer(request, customer):
                log_account_action(request, customer, 'Portal welcome email sent')
                messages.success(request, _('Welcome email sent to %(email)s.') % {'email': customer.contact_email})
            else:
                messages.error(request, taken % {'email': customer.contact_email})
            return _account_redirect(request, customer)
        form = AddPortalUserForm(request.POST)
        if not form.is_valid():
            messages.error(request, ' '.join(error for errors in form.errors.values() for error in errors))
            return _account_redirect(request, customer)
        person = add_person(customer, form.cleaned_data['email'], form.cleaned_data['name'])
        send_welcome(request, person)
        log_account_action(request, customer, f'Portal login added for {person.user.email}; welcome email sent')
        messages.success(request, _('%(name)s can now sign in: a welcome email was sent to %(email)s.') % {
            'name': person.name, 'email': person.user.email,
        })
        return _account_redirect(request, customer)

    person = get_object_or_404(
        PortalUser.objects.select_related('user', 'customer__country'), pk=request.POST.get('person'),
        customer__is_active=True,
    )
    customer, user = person.customer, person.user
    status = account_status(person, locked)
    if action == 'resend' and status in (AccountStatus.INVITED, AccountStatus.EXPIRED):
        send_welcome(request, person)
        log_account_action(request, customer, f'Portal welcome email sent to {user.email}')
        messages.success(request, _('Welcome email sent to %(email)s.') % {'email': user.email})
    elif action == 'copy_welcome_link' and status in (AccountStatus.INVITED, AccountStatus.EXPIRED):
        # The welcome email's own link, shown to copy and send another way.
        link = welcome_link(request, person)
        log_account_action(request, customer, f'Portal welcome link copied for {user.email}')
        return render(request, 'customers/customer_account_link.html', {
            'customer': customer, 'person': person, 'link': link, 'days': LINK_VALID_DAYS,
            'portal_address': settings.PORTAL_HOST or request.get_host(),
        })
    elif action == 'password_link' and status in (AccountStatus.ACTIVE, AccountStatus.LOCKED):
        send_password_link(request, person)
        log_account_action(request, customer, f'Portal password link sent to {user.email}')
        messages.success(request, _('A password link has been sent to %(email)s.') % {'email': user.email})
    elif action == 'unlock':
        axes_reset(username=user.username)
        log_account_action(request, customer, f'Portal login unlocked: {user.email}')
        messages.success(request, _('%(name)s has been unlocked.') % {'name': person.name})
    elif action in ('disable', 'enable'):
        user.is_active = action == 'enable'
        user.save(update_fields=['is_active'])
        log_account_action(
            request, customer, f'Portal access {"enabled" if user.is_active else "disabled"}: {user.email}',
        )
        messages.success(request, _('Portal access enabled.') if user.is_active else _('Portal access disabled.'))
    elif action == 'change_email':
        if not can_set_password(request.user.technician):
            raise PermissionDenied
        form = ChangeLoginEmailForm(request.POST, user=user)
        if form.is_valid():
            old_email = user.email
            user.username = user.email = form.cleaned_data['email']
            user.save(update_fields=['username', 'email'])
            log_account_action(request, customer, f'Portal login email changed from {old_email} to {user.email}')
            # The old address is told, in case it wasn't them who asked.
            if old_email and old_email.lower() != user.email.lower():
                send_email_changed_notice(person, old_email)
            messages.success(request, _('Login email changed to %(email)s.') % {'email': user.email})
        else:
            messages.error(request, form.errors['email'][0])
    elif action == 'remove':
        # Their login stops working; what they sent keeps their name (user.first_name).
        user.is_active = False
        user.save(update_fields=['is_active'])
        email = user.email
        person.delete()
        log_account_action(request, customer, f'Portal login removed: {email}')
        messages.success(request, _('%(email)s can no longer sign in.') % {'email': email})
    elif action == 'temporary_password' and user.is_active:
        if not can_set_password(request.user.technician):
            raise PermissionDenied
        # Shown once, right here, for reading out over the phone — never
        # emailed, never stored anywhere readable.
        password = _generate_temporary_password()
        user.set_password(password)
        user.save(update_fields=['password'])
        person.must_change_password = True
        person.save(update_fields=['must_change_password'])
        log_account_action(request, customer, f'Portal temporary password set for {user.email}')
        return render(request, 'customers/customer_account_password.html', {
            'customer': customer, 'person': person, 'password': password,
        })
    return _account_redirect(request, customer)


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
            request, user.portal_user.customer, f'Portal sign-in email changed by {old_email} to {new_email}',
            user=user,
        )
        if old_email:
            send_email_changed_notice(user.portal_user, old_email)
        messages.success(request, _('Your sign-in email is now %(email)s.') % {'email': new_email})
    if request.user.is_authenticated and hasattr(request.user, 'portal_user'):
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
    customer = user.portal_user.customer
    first_time = user.portal_user.must_change_password

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
        person = PortalUser.objects.filter(
            customer__is_active=True, user__is_active=True, user__username__iexact=form.cleaned_data['email'],
        ).select_related('user', 'customer__country').first()
        # One link per address in a while (spots.throttle) — the page says the same either way.
        if person is not None and link_email_allowed(person.user.email):
            if person.must_change_password:
                send_welcome(request, person)
            else:
                send_password_link(request, person)
        return render(request, 'customers/portal_password_help.html', {'sent': True})
    return render(request, 'customers/portal_password_help.html', {'form': form})
