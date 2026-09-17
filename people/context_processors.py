from .models import RolePermission


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
