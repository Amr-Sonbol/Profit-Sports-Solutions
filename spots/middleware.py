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
VIEW_AS_USER_SESSION_KEY = 'view_as_user'


class ViewAsCustomerMiddleware:
    """An admin seeing the site exactly as one customer, or one staff member,
    sees it — to check a complaint — without touching that account: no
    password change, no login on it, nothing sent, nothing marked read.

    While the session holds VIEW_AS_SESSION_KEY (a customer:
    customers.views.view_as_start) or VIEW_AS_USER_SESSION_KEY (a staff
    member who isn't an admin: tasks.views.view_as_user_start), each request
    runs as that login (`request.user`), with the admin kept as
    `request.real_user` and the person as `request.viewing_as` (a customer)
    or `request.viewing_as_user` (a technician). View only: anything but
    reading is refused, except stopping. Anyone but an admin simply ends it.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.viewing_as = None
        request.viewing_as_user = None
        session = getattr(request, 'session', None)
        customer_id = session.get(VIEW_AS_SESSION_KEY) if session is not None else None
        user_id = session.get(VIEW_AS_USER_SESSION_KEY) if session is not None else None
        if customer_id or user_id:
            from django.contrib import messages
            from django.shortcuts import redirect
            from django.urls import reverse
            from django.utils.translation import gettext as _

            from customers.models import Customer
            from people.models import Technician

            technician = getattr(request.user, 'technician', None)
            is_admin = technician is not None and technician.role == Technician.Role.ADMIN
            customer = person = None
            if is_admin and customer_id:
                customer = Customer.objects.select_related('user', 'country').filter(
                    pk=customer_id, user__isnull=False, user__is_active=True,
                ).first()
            elif is_admin and user_id:
                person = Technician.objects.select_related('user', 'country').filter(
                    user_id=user_id, user__is_active=True,
                ).exclude(role=Technician.Role.ADMIN).first()
            if customer is None and person is None:
                session.pop(VIEW_AS_SESSION_KEY, None)
                session.pop(VIEW_AS_USER_SESSION_KEY, None)
            else:
                stop_url = reverse('customers:view_as_stop')
                if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.path != stop_url:
                    messages.error(request, _('View only: nothing can be sent or changed while viewing as someone else.'))
                    return redirect(request.META.get('HTTP_REFERER') or reverse('home'))
                request.real_user = request.user
                request.user = (customer or person).user
                request.viewing_as = customer
                request.viewing_as_user = person
        return self.get_response(request)
