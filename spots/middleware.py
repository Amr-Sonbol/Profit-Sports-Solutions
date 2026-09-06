from django.utils import timezone, translation


class TechnicianLocaleMiddleware:
    """Activate the signed-in technician's language and country timezone.

    Language is per person and timezone is per country (see CLAUDE.md), so
    both come from the technician profile rather than the browser default.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        technician = getattr(request.user, 'technician', None)
        if technician is not None:
            translation.activate(technician.language)
            request.LANGUAGE_CODE = technician.language
            timezone.activate(technician.country.timezone)

        response = self.get_response(request)

        if technician is not None:
            translation.deactivate()
            timezone.deactivate()

        return response
