from django.utils import timezone, translation

from people.permissions import get_active_country


class TechnicianLocaleMiddleware:
    """Activate the signed-in technician's language and country timezone.

    Language is per person; timezone is per country (see CLAUDE.md) — a
    manager's own country by default, or whichever one they've switched
    to with the header country switcher, so timestamps still display in
    that country's local time while they're looking at its data.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        technician = getattr(request.user, 'technician', None)
        if technician is not None:
            translation.activate(technician.language)
            request.LANGUAGE_CODE = technician.language
            timezone.activate(get_active_country(request).timezone)

        response = self.get_response(request)

        if technician is not None:
            translation.deactivate()
            timezone.deactivate()

        return response
