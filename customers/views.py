import csv
import io

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm, UserCreationForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from people.models import RolePermission
from people.permissions import get_active_country, require_manager, require_permission
from tasks.forms import CustomerPortalTicketForm
from tasks.models import CustomerTicketAttachment, Task
from tasks.views import save_new_ticket

from .forms import CustomerCreateForm, CustomerEditForm, CustomerImportForm, SiteCreateForm, SiteEditForm
from .models import Customer, Site

CUSTOMER_IMPORT_REQUIRED_COLUMNS = ['customer_name', 'site_name', 'site_address']
CUSTOMER_IMPORT_COLUMNS = CUSTOMER_IMPORT_REQUIRED_COLUMNS + [
    'segment', 'customer_contact_name', 'customer_contact_phone', 'customer_contact_email',
    'site_contact_name', 'site_contact_phone', 'site_contact_email', 'access_notes',
]


@login_required
def customer_list(request):
    """Every customer in the viewer's active country — gyms, hotels, clubs,
    whatever "other" turns out to mean — with a way to add a new one.
    """
    require_permission(request, RolePermission.Permission.MANAGE_CUSTOMERS)

    search = request.GET.get('q', '').strip()
    customers = Customer.objects.filter(country=get_active_country(request)).prefetch_related('sites')
    if search:
        customers = customers.filter(name__icontains=search)

    return render(request, 'customers/customer_list.html', {'customers': customers, 'search': search})


@login_required
def customer_create(request):
    require_permission(request, RolePermission.Permission.MANAGE_CUSTOMERS)

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
    """The customer's sites, and a way to add another one — the same
    country scoping used everywhere else a supervisor looks at one record.
    """
    require_permission(request, RolePermission.Permission.MANAGE_CUSTOMERS)
    customer = get_object_or_404(Customer, pk=pk, country=get_active_country(request))

    if request.method == 'POST':
        form = SiteCreateForm(request.POST, customer=customer)
        if form.is_valid():
            site = form.save(commit=False)
            site.customer = customer
            site.save()
            messages.success(request, _('Site added.'))
            return redirect('customers:customer_detail', pk=customer.pk)
    else:
        form = SiteCreateForm(customer=customer)

    context = {'customer': customer, 'sites': customer.sites.all(), 'form': form}
    return render(request, 'customers/customer_detail.html', context)


@login_required
def customer_edit(request, pk):
    requesting_technician = require_permission(request, RolePermission.Permission.MANAGE_CUSTOMERS)
    is_manager = requesting_technician.is_manager_tier
    customer = get_object_or_404(Customer, pk=pk, country=get_active_country(request))

    form = CustomerEditForm(instance=customer)
    login_form = UserCreationForm() if is_manager and customer.user_id is None else None
    password_form = SetPasswordForm(user=customer.user) if is_manager and customer.user_id else None

    if request.method == 'POST' and request.POST.get('action') == 'create_login' and login_form is not None:
        login_form = UserCreationForm(request.POST)
        if login_form.is_valid():
            user = login_form.save()
            if customer.contact_email:
                user.email = customer.contact_email
                user.save(update_fields=['email'])
            customer.user = user
            customer.save(update_fields=['user'])
            messages.success(request, _('Login created.'))
            return redirect('customers:customer_edit', pk=customer.pk)

    elif request.method == 'POST' and request.POST.get('action') == 'reset_password' and password_form is not None:
        password_form = SetPasswordForm(user=customer.user, data=request.POST)
        if password_form.is_valid():
            password_form.save()
            messages.success(request, _('Password reset.'))
            return redirect('customers:customer_edit', pk=customer.pk)

    elif request.method == 'POST':
        form = CustomerEditForm(request.POST, instance=customer)
        if form.is_valid():
            form.save()
            messages.success(request, _('Customer updated.'))
            return redirect('customers:customer_detail', pk=customer.pk)

    context = {
        'customer': customer, 'form': form, 'login_form': login_form,
        'password_form': password_form, 'is_manager': is_manager,
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
    in one file. Matches an existing customer/site by name instead of
    duplicating it, so the same file can be re-run to add new branches
    without recreating what's already there.
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

        if not customer_name or not site_name or not site_address:
            results.append({
                **result, 'outcome': 'error',
                'detail': _('customer_name, site_name, and site_address are required'),
            })
            continue

        segment = (row.get('segment') or '').strip().lower() or Customer.Segment.GYM
        if segment not in valid_segments:
            results.append({
                **result, 'outcome': 'error',
                'detail': _('Unknown segment "%(segment)s" — use gym, hotel, club, or other') % {'segment': segment},
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

                if Site.objects.filter(customer=customer, name__iexact=site_name).exists():
                    results.append({
                        **result, 'outcome': 'skipped',
                        'detail': _('This customer already has a site with that name'),
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
    one-by-one. Manager-only, same fixed floor as Skills/Countries/
    Roles: a bad file could create a lot of rows fast.
    """
    require_manager(request)

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
    writer.writerow([
        'Fitness First', 'gym', 'Ali Manager', '0501234567', 'ali@example.com',
        'Marina Branch', 'Dubai Marina, near the mall', '', '', '', 'Gate code 1234',
    ])
    writer.writerow([
        'Fitness First', 'gym', '', '', '',
        'JBR Branch', 'JBR, near the beach', '', '', '', '',
    ])
    return response


def require_customer(request):
    """Restrict a view to a customer's own portal login — the customer-
    side counterpart to people.permissions.require_technician."""
    customer = getattr(request.user, 'customer', None)
    if customer is None:
        raise PermissionDenied
    return customer


def portal_login(request):
    """A customer's own login page — same underlying auth as the staff
    one (django.contrib.auth), just branded for them and rejecting
    anything that isn't a customer account, like a staff login typed in
    here by mistake.
    """
    if request.user.is_authenticated:
        return redirect('customers:portal_home' if hasattr(request.user, 'customer') else 'home')

    next_url = request.POST.get('next') or request.GET.get('next') or ''

    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        if hasattr(user, 'customer'):
            login(request, user)
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
            return redirect('customers:portal_home')
        form.add_error(None, _("This isn't a customer account."))

    return render(request, 'customers/portal_login.html', {'form': form, 'next': next_url})


@login_required(login_url='customers:portal_login')
def portal_home(request):
    """A logged-in customer's own tickets and service history — summary
    only (date, site, type, status). Never the internal report detail,
    technician names, or parts used that staff sees on the same task.
    """
    customer = require_customer(request)
    tickets = customer.tickets.order_by('-submitted_at')
    visits = Task.objects.filter(site__customer=customer).select_related('site', 'task_type').order_by(
        '-scheduled_for', '-reported_at',
    )
    return render(request, 'customers/portal_home.html', {'customer': customer, 'tickets': tickets, 'visits': visits})


@login_required(login_url='customers:portal_login')
def portal_ticket_new(request):
    """A logged-in customer reporting an issue — the customer and site
    are already known, so this skips straight to the issue itself and
    links the ticket to this customer immediately, no matching needed.
    """
    customer = require_customer(request)

    if request.method == 'POST':
        form = CustomerPortalTicketForm(request.POST, request.FILES, customer=customer)
        if form.is_valid():
            site = form.cleaned_data['site']
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
                save_new_ticket(ticket)
                for uploaded_file in form.cleaned_data['attachments']:
                    CustomerTicketAttachment.objects.create(
                        ticket=ticket, file=uploaded_file, uploaded_at=timezone.now(),
                    )
            messages.success(request, _('Ticket %(number)s submitted.') % {'number': ticket.ticket_number})
            return redirect('customers:portal_home')
    else:
        form = CustomerPortalTicketForm(customer=customer, initial={
            'contact_name': customer.contact_name, 'contact_phone': customer.contact_phone,
            'contact_email': customer.contact_email, 'shipping_address': customer.shipping_address,
        })

    return render(request, 'customers/portal_ticket_new.html', {'form': form})
