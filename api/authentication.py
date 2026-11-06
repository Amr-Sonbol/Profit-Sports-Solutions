from django.utils import timezone
from rest_framework.authentication import TokenAuthentication

from reference.models import Country

# The country a manager chose in the app (its id), sent with every request
# — the app's version of the website's header switcher (the session).
ACTIVE_COUNTRY_HEADER = 'HTTP_X_ACTIVE_COUNTRY'


class CountryTimezoneTokenAuthentication(TokenAuthentication):
    """The app's token sign-in, plus what the website's
    TechnicianLocaleMiddleware does for a signed-in page: the person's
    country timezone, so "today", a week or a month in the app is theirs,
    not UTC's — and for a manager, the country they chose in the app. (The middleware runs before DRF reads the token, so it
    can't do this itself; it resets the timezone after every request.)
    The app's own wording stays English, so the language is left alone.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            technician = getattr(result[0], 'technician', None)
            if technician is not None:
                country = technician.country
                # A manager looking at another country (people.permissions
                # .get_active_country reads it, like the website's session).
                chosen = request.META.get(ACTIVE_COUNTRY_HEADER, '')
                if technician.is_manager_tier and chosen.isdigit():
                    country = Country.objects.filter(pk=chosen).first() or country
                    request.api_active_country = country
                timezone.activate(country.timezone)
        return result
