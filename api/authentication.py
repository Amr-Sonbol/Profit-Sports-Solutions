from django.utils import timezone
from rest_framework.authentication import TokenAuthentication


class CountryTimezoneTokenAuthentication(TokenAuthentication):
    """The app's token sign-in, plus what the website's
    TechnicianLocaleMiddleware does for a signed-in page: the person's
    country timezone, so "today", a week or a month in the app is theirs,
    not UTC's. (The middleware runs before DRF reads the token, so it
    can't do this itself; it resets the timezone after every request.)
    The app's own wording stays English, so the language is left alone.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            technician = getattr(result[0], 'technician', None)
            if technician is not None:
                timezone.activate(technician.country.timezone)
        return result
