from reference.models import Country

from .models import RolePermission
from .permissions import get_active_country


def role_permissions(request):
    """The signed-in technician's currently-allowed permission codes, for
    templates — so nav links follow the same configurable rules the views
    themselves enforce, instead of a second, separately-hardcoded set of
    role checks that could drift out of sync with it.
    """
    technician = getattr(request.user, 'technician', None)
    if technician is None:
        return {}
    allowed = set(
        RolePermission.objects.filter(role=technician.role, allowed=True).values_list('permission', flat=True),
    )
    return {'role_permissions': allowed}


def active_country(request):
    """Which country every screen is currently scoped to, and — for a
    manager only — the full list to switch between in the header. Empty
    for everyone else, so the switcher template block simply doesn't
    render rather than showing a single fixed, unchangeable option.
    """
    technician = getattr(request.user, 'technician', None)
    if technician is None:
        return {}
    context = {'active_country': get_active_country(request)}
    if technician.role == technician.Role.MANAGER:
        context['switchable_countries'] = Country.objects.filter(is_active=True).order_by('name')
    return context
