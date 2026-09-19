from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from people.models import RolePermission
from people.permissions import get_active_country, require_permission

from .forms import CustomerCreateForm, CustomerEditForm, SiteCreateForm, SiteEditForm
from .models import Customer, Site


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
    require_permission(request, RolePermission.Permission.MANAGE_CUSTOMERS)
    customer = get_object_or_404(Customer, pk=pk, country=get_active_country(request))

    if request.method == 'POST':
        form = CustomerEditForm(request.POST, instance=customer)
        if form.is_valid():
            form.save()
            messages.success(request, _('Customer updated.'))
            return redirect('customers:customer_detail', pk=customer.pk)
    else:
        form = CustomerEditForm(instance=customer)

    return render(request, 'customers/customer_edit.html', {'customer': customer, 'form': form})


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
