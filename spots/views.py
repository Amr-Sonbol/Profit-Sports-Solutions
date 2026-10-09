from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.shortcuts import redirect, render


@require_POST
def set_language(request):
    """The header language toggle posts here — same shape as Django's own
    django.views.i18n.set_language (this URL replaces it, same name, so
    no template needs to know), but for a signed-in technician or
    customer it updates their saved language field instead of just the
    session. spots.middleware.TechnicianLocaleMiddleware re-activates
    that stored language on every request for them, so a session-only
    change would silently revert on the very next page load — this is
    the same preference My Profile's own language field already sets,
    just reachable from anywhere. An anonymous visitor (the login pages)
    still gets the ordinary session/cookie switch, since there's no
    profile yet to save it to.
    """
    language = request.POST.get('language')
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or '/'
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = '/'
    response = HttpResponseRedirect(next_url)
    if not language or language not in dict(settings.LANGUAGES):
        return response

    technician = getattr(request.user, 'technician', None)
    customer = getattr(request.user, 'customer', None)
    if technician is not None:
        technician.language = language
        technician.save(update_fields=['language'])
    elif customer is not None:
        customer.language = language
        customer.save(update_fields=['language'])
    else:
        translation.activate(language)
        response.set_cookie(
            settings.LANGUAGE_COOKIE_NAME, language,
            max_age=settings.LANGUAGE_COOKIE_AGE, path=settings.LANGUAGE_COOKIE_PATH,
            domain=settings.LANGUAGE_COOKIE_DOMAIN, secure=settings.LANGUAGE_COOKIE_SECURE,
            httponly=settings.LANGUAGE_COOKIE_HTTPONLY, samesite=settings.LANGUAGE_COOKIE_SAMESITE,
        )
    return response


@login_required
def home(request):
    """Land any staff login on the welcome page — an icon tile for every
    page this person can open, drawn from the same list as the header
    menu (people.context_processors.nav_pages), so each role sees only
    its own pages. A customer login lands on their own portal instead.
    One shared login page for every account type — this is the only
    place that has to know how to route each of them afterward. Either
    kind, still on a temporary system-generated password, is routed to
    set a real one first — same reasoning as customers.views.portal_login,
    just the staff-side entry point.
    """
    customer = getattr(request.user, 'customer', None)
    if customer is not None:
        if customer.must_change_password:
            return redirect('customers:portal_first_login')
        return redirect('customers:portal_home')
    technician = getattr(request.user, 'technician', None)
    if technician is None:
        return redirect('tasks:dashboard')
    if technician.must_change_password:
        return redirect('tasks:first_login')
    return render(request, 'welcome.html')
