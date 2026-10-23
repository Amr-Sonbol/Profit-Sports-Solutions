from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from people.models import RolePermission


class IsTechnician(BasePermission):
    """Any signed-in technician, any role — same floor as require_technician.
    Someone still on a temporary password gets nothing but the views that
    set `allow_temporary_password` until they choose their own.
    """

    def has_permission(self, request, view):
        technician = getattr(request.user, 'technician', None)
        if technician is None:
            return False
        if technician.must_change_password and not getattr(view, 'allow_temporary_password', False):
            raise PermissionDenied('Choose your own password first.')
        return True


def has_role_permission(request, permission):
    technician = getattr(request.user, 'technician', None)
    if technician is None:
        return False
    return RolePermission.objects.filter(role=technician.role, permission=permission, allowed=True).exists()


def require_role_permission(permission):
    """Same check as people.permissions.require_permission, as a DRF
    permission class factory — DRF instantiates permission_classes with
    no arguments, so the permission to check is baked in at class-creation
    time instead of passed to __init__.
    """

    class _RequiresRolePermission(BasePermission):
        def has_permission(self, request, view):
            # Same temporary-password gate as IsTechnician, first.
            return IsTechnician().has_permission(request, view) and has_role_permission(request, permission)

    return _RequiresRolePermission
