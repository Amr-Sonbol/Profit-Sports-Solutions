"""The customer portal on its own address, apart from the staff site
(settings.PORTAL_HOST, e.g. portal.example.com). Each address keeps its own
cookies, so a customer's session and a staff member's never mix; on the
portal's address only the portal's pages open, and the staff address sends
any portal link there, so old links keep working. Empty PORTAL_HOST: one
address for both, as before — nothing here does anything.
"""
from django.conf import settings
from django.http import Http404, HttpResponseRedirect

# What a customer reaches: the portal, a request's status page and the
# rating page (both from emails, by token), switching language, signing out,
# and the files and styles those pages use.
PORTAL_PATHS = ('/customers/portal/', '/tasks/tickets/status/', '/reports/feedback/')
SHARED_PATHS = ('/i18n/', '/accounts/logout/', '/static/', '/media/', '/favicon')


def _on_portal_host(request):
    host = settings.PORTAL_HOST
    return bool(host) and request.get_host().lower() == host.lower()


def portal_url(request, path):
    """A link for a customer: on the portal's address when it has one,
    whichever address the email was sent from (a staff action, say)."""
    if settings.PORTAL_HOST:
        scheme = request.scheme if request is not None else 'https'
        return f'{scheme}://{settings.PORTAL_HOST}{path}'
    return request.build_absolute_uri(path)


class PortalHostMiddleware:
    """On the portal's address: the portal's pages only (its root opens the
    portal), anything else not found. On the staff address: a portal page
    moves to the portal's address — except while an admin is viewing the
    portal as a customer (spots.middleware), which happens on the staff
    address, signed in as the admin."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.PORTAL_HOST:
            return self.get_response(request)
        path = request.path
        if _on_portal_host(request):
            if path == '/':
                return HttpResponseRedirect('/customers/portal/')
            if not path.startswith(PORTAL_PATHS + SHARED_PATHS):
                raise Http404
        elif path.startswith(PORTAL_PATHS):
            from spots.middleware import VIEW_AS_SESSION_KEY

            if not request.session.get(VIEW_AS_SESSION_KEY):
                return HttpResponseRedirect(portal_url(request, request.get_full_path()))
        return self.get_response(request)
