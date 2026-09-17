from django.core.exceptions import PermissionDenied

from reference.models import Country

from .models import RolePermission, Technician

ACTIVE_COUNTRY_SESSION_KEY = 'active_country_id'


def require_permission(request, permission):
    """Restrict a view to technicians whose role currently has this
    permission enabled. Configurable per role from the Roles & permissions
    screen — nothing here is hardcoded to a specific role.
    """
    technician = getattr(request.user, 'technician', None)
    if technician is None:
        raise PermissionDenied
    if not RolePermission.objects.filter(
        role=technician.role, permission=permission, allowed=True,
    ).exists():
        raise PermissionDenied
    return technician


def require_technician(request):
    """Restrict a view to any signed-in technician (own-record screens, any role)."""
    technician = getattr(request.user, 'technician', None)
    if technician is None:
        raise PermissionDenied
    return technician


def get_active_country(request):
    """The country every screen scopes to for this request. A manager can
    switch this away from their own home country via the header switcher
    (session-only, never changes their actual technician.country); every
    other role always sees their own — there's no override to check.
    """
    technician = getattr(request.user, 'technician', None)
    if technician is None:
        return None
    if technician.role == Technician.Role.MANAGER:
        active_id = request.session.get(ACTIVE_COUNTRY_SESSION_KEY)
        if active_id:
            country = Country.objects.filter(pk=active_id).first()
            if country is not None:
                return country
    return technician.country


def require_manager(request):
    """Restrict a view to managers only — a fixed floor, not part of the
    configurable permission system it manages (the Roles & permissions
    screen itself). If that screen were subject to its own toggles, a bad
    edit could lock every role out of fixing it.
    """
    technician = getattr(request.user, 'technician', None)
    if technician is None or technician.role != Technician.Role.MANAGER:
        raise PermissionDenied
    return technician
