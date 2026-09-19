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
            timezone.deactivate()

        return response
