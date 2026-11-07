"""Telling staff when an email couldn't be sent. Emails go out in the
background, after the page has already said "done", so a failure is
recorded (people.FailedEmail) and shown once, on the next page, to the staff
member whose action sent it; one with nobody behind it (a customer's own
request, say) goes to the admins. Customers never see these.
"""
from contextvars import ContextVar

from django.utils import timezone

_current_user_id = ContextVar('current_user_id', default=None)


def current_user_id():
    """The signed-in person making the current request, if any."""
    return _current_user_id.get()


def record_failed_email(triggered_by_id, recipients, subject, error):
    from people.models import FailedEmail, Technician

    # Only a staff member is told; a customer's own action goes to the admins.
    if triggered_by_id and not Technician.objects.filter(user_id=triggered_by_id).exists():
        triggered_by_id = None
    FailedEmail.objects.create(
        triggered_by_id=triggered_by_id, recipients=', '.join(recipients), subject=subject[:255],
        error=str(error)[:500] or type(error).__name__, failed_at=timezone.now(),
    )


class CurrentUserMiddleware:
    """Notes who is making the request, for an email sent from it."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        token = _current_user_id.set(user.pk if user is not None and user.is_authenticated else None)
        try:
            return self.get_response(request)
        finally:
            _current_user_id.reset(token)


def failed_emails(request):
    """Context processor: this staff member's unseen failed emails (an admin's
    also those with nobody behind them), marked seen as they're shown."""
    from people.models import FailedEmail, Technician

    user = getattr(request, 'user', None)
    technician = getattr(user, 'technician', None) if user is not None and user.is_authenticated else None
    if technician is None or getattr(request, 'viewing_as', None) or getattr(request, 'viewing_as_user', None):
        return {}
    unseen = FailedEmail.objects.filter(seen_at__isnull=True, triggered_by=user)
    if technician.role == Technician.Role.ADMIN:
        unseen = FailedEmail.objects.filter(seen_at__isnull=True).filter(
            triggered_by=user,
        ) | FailedEmail.objects.filter(seen_at__isnull=True, triggered_by__isnull=True)
    shown = list(unseen.order_by('failed_at')[:5])
    if shown:
        FailedEmail.objects.filter(pk__in=[failure.pk for failure in shown]).update(seen_at=timezone.now())
    return {'failed_emails': shown}
