from django.utils import timezone, translation

from people.permissions import get_active_country


class TechnicianLocaleMiddleware:
    """Activate the signed-in user's language and country timezone —
    a technician's own, or a customer's own, whichever this login is.

    Language is per person; timezone is per country (see CLAUDE.md) — a
    manager's own country by default, or whichever one they've switched
    to with the header country switcher, so timestamps still display in
    that country's local time while they're looking at its data. A
    customer has no switcher — always their own country.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        technician = getattr(request.user, 'technician', None)
        customer = getattr(request.user, 'customer', None)
        person = technician or customer
        if person is not None:
            translation.activate(person.language)
            request.LANGUAGE_CODE = person.language
            country = get_active_country(request) if technician is not None else customer.country
            timezone.activate(country.timezone)

        response = self.get_response(request)

        if person is not None:
            translation.deactivate()
        # Always: an app request activates its timezone only after the
        # token is read (api.authentication), past this point.
        timezone.deactivate()

        return response


VIEW_AS_SESSION_KEY = 'view_as_customer'


class ViewAsCustomerMiddleware:
    """An admin seeing the portal exactly as one customer sees it, to check
    a complaint — without touching the customer's account: no password
    change, no login on their account, nothing sent to them.

    While the session holds VIEW_AS_SESSION_KEY (customers.views
    view_as_start), each request runs as that customer's login
    (`request.user`), with the admin kept as `request.real_user` and the
    customer as `request.viewing_as`. View only: anything but reading is
    refused, except stopping. A non-admin, or a customer without a login,
    simply ends it.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        customer_id = request.session.get(VIEW_AS_SESSION_KEY) if hasattr(request, 'session') else None
        request.viewing_as = None
        if customer_id:
            from django.contrib import messages
            from django.shortcuts import redirect
            from django.urls import reverse
            from django.utils.translation import gettext as _

            from customers.models import Customer
            from people.models import Technician

            technician = getattr(request.user, 'technician', None)
            customer = Customer.objects.select_related('user', 'country').filter(
                pk=customer_id, user__isnull=False, user__is_active=True,
            ).first()
            if technician is None or technician.role != Technician.Role.ADMIN or customer is None:
                request.session.pop(VIEW_AS_SESSION_KEY, None)
            else:
                stop_url = reverse('customers:view_as_stop')
                if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.path != stop_url:
                    messages.error(request, _('View only: nothing can be sent or changed while viewing as a customer.'))
                    return redirect(request.META.get('HTTP_REFERER') or reverse('customers:portal_home'))
                request.real_user = request.user
                request.user = customer.user
                request.viewing_as = customer
        return self.get_response(request)
