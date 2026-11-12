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
        from customers.models import portal_customer

        technician = getattr(request.user, 'technician', None)
        customer = portal_customer(request.user)
        person = technician or customer
        if person is not None:
            # A portal person's own language, or their company's.
            language = technician.language if technician is not None else request.user.portal_user.portal_language
            translation.activate(language)
            request.LANGUAGE_CODE = language
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

            from customers.models import PortalUser
            from people.models import Technician

            technician = getattr(request.user, 'technician', None)
            is_admin = technician is not None and technician.role == Technician.Role.ADMIN
            customer = person = None
            customer_login = None
            if is_admin and customer_id:
                # One of its people who can sign in: everyone sees the whole customer, the same.
                customer_login = PortalUser.objects.select_related('user', 'customer__country').filter(
                    customer_id=customer_id, user__is_active=True,
                ).first()
                customer = customer_login.customer if customer_login else None
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
                request.user = customer_login.user if customer is not None else person.user
                request.viewing_as = customer
                request.viewing_as_user = person
        return self.get_response(request)


class ContentSecurityPolicyMiddleware:
    """Tells the browser where a page may load things from, so a script that
    found its way into a page (a name, a message) can't run. Scripts only
    from this site, unpkg (the team map's Leaflet) and the page's own inline
    ones carrying this request's nonce (`request.csp_nonce` —
    <script nonce="{{ request.csp_nonce }}">); no inline event attributes
    (static/js/page-actions.js has those). Styles from this site, Google
    Fonts and unpkg — inline style attributes allowed (a portal's own color).
    Images also from the map tiles, and blob:/data: for photos picked to
    upload. Never framed; forms post only here.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        import secrets

        request.csp_nonce = secrets.token_urlsafe(16)
        response = self.get_response(request)
        if 'text/html' in response.get('Content-Type', '') and 'Content-Security-Policy' not in response:
            response['Content-Security-Policy'] = '; '.join([
                "default-src 'self'",
                f"script-src 'self' 'nonce-{request.csp_nonce}' https://unpkg.com",
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com",
                "font-src 'self' https://fonts.gstatic.com",
                "img-src 'self' data: blob: https://tile.openstreetmap.org https://*.tile.openstreetmap.org",
                "media-src 'self' blob:",
                "connect-src 'self'",
                "object-src 'none'",
                "base-uri 'self'",
                f"form-action 'self'{_own_addresses()}",
                "frame-ancestors 'none'",
            ])
        return response


def _own_addresses():
    """The portal's and the staff site's own addresses, when they're apart
    (settings.PORTAL_HOST / STAFF_HOST): a form on one may end on the other."""
    from django.conf import settings

    hosts = [host for host in (settings.PORTAL_HOST, settings.STAFF_HOST) if host]
    return ''.join(f' https://{host} http://{host}' if settings.DEBUG else f' https://{host}' for host in hosts)
