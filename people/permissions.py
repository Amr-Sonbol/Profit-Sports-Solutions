from django.core.exceptions import PermissionDenied

from .models import Technician


def require_supervisor(request):
    """Restrict a view to supervisors/managers. Raises PermissionDenied otherwise."""
    technician = getattr(request.user, 'technician', None)
    if technician is None or technician.role not in (Technician.Role.SUPERVISOR, Technician.Role.MANAGER):
        raise PermissionDenied
    return technician


def require_technician(request):
    """Restrict a view to any signed-in technician (own-record screens, any role)."""
    technician = getattr(request.user, 'technician', None)
    if technician is None:
        raise PermissionDenied
    return technician
