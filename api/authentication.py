from datetime import timedelta

from django.utils import timezone, translation
from django.utils.translation import gettext_lazy as _
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import AuthenticationFailed

from reference.models import Country

# The country a manager chose in the app (its id), sent with every request
# — the app's version of the website's header switcher (the session).
ACTIVE_COUNTRY_HEADER = 'HTTP_X_ACTIVE_COUNTRY'

# An app sign-in ends after this long unused: a lost or forgotten phone signs
# itself out. Each use renews it (Token.created, moved forward at most once a
# day, so it costs nothing per request): someone using the app never notices.
TOKEN_IDLE_DAYS = 30


def renew(token, now=None):
    """Mark the token as used now (signing in, or a day's first request)."""
    now = now or timezone.now()
    Token.objects.filter(pk=token.pk).update(created=now)
    token.created = now


class CountryTimezoneTokenAuthentication(TokenAuthentication):
    """The app's token sign-in, plus what the website's
    TechnicianLocaleMiddleware does for a signed-in page: the person's
    country timezone, so "today", a week or a month in the app is theirs,
    not UTC's — and for a manager, the country they chose in the app. (The middleware runs before DRF reads the token, so it
    can't do this itself; it resets the timezone after every request.)
    And the app's language — their country's (Technician.app_language) — so
    what the server says (status names, messages) is in the app's language.
    """

    def authenticate_credentials(self, key):
        """The token's person — unless it went unused for TOKEN_IDLE_DAYS: then
        every phone of theirs is signed out (as Staff accounts' "Sign out of
        all devices" does: the token and their phones' notifications) and the
        app shows the sign-in again."""
        user, token = super().authenticate_credentials(key)
        now = timezone.now()
        if now - token.created > timedelta(days=TOKEN_IDLE_DAYS):
            from tasks.views import sign_out_of_mobile_app

            sign_out_of_mobile_app(user)
            raise AuthenticationFailed(_('You were signed out after 30 days without using the app. Please sign in again.'))
        if now - token.created > timedelta(days=1):
            renew(token, now)
        return user, token

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            # The app's requests sign in here, after the middleware: the audit trail is told who (spots.audit).
            from spots.audit import set_actor
            set_actor(result[0])
            technician = getattr(result[0], 'technician', None)
            if technician is not None:
                country = technician.country
                # A manager looking at another country (people.permissions
                # .get_active_country reads it, like the website's session).
                chosen = request.META.get(ACTIVE_COUNTRY_HEADER, '')
                if technician.reaches_all_countries and chosen.isdigit():
                    country = Country.objects.filter(pk=chosen).first() or country
                    request.api_active_country = country
                timezone.activate(country.timezone)
                translation.activate(technician.app_language)
                request.LANGUAGE_CODE = technician.app_language
        return result
