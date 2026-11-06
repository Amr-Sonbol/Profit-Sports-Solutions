from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

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
    # The app's choice (api.authentication, for the manager tier only).
    chosen_in_app = getattr(request, 'api_active_country', None)
    if chosen_in_app is not None:
        return chosen_in_app
    if technician.is_manager_tier:
        active_id = request.session.get(ACTIVE_COUNTRY_SESSION_KEY)
        if active_id:
            country = Country.objects.filter(pk=active_id).first()
            if country is not None:
                return country
    return technician.country


def require_manager(request):
    """Restrict a view to the manager tier (manager or admin) — a fixed
    floor, not part of the configurable permission system it manages.
    Admin is a superset of manager, so this stays the shared gate for
    every manager-only screen except Roles & permissions itself (see
    require_admin).
    """
    technician = getattr(request.user, 'technician', None)
    if technician is None or not technician.is_manager_tier:
        raise PermissionDenied
    return technician


def scoped_or_404(queryset, pk, requesting_technician, active_country, country_lookup):
    """A manager can open any task, technician, or customer regardless of
    their own active country — the point of the all-countries task list
    and the all_technicians/all_customers boards is reaching across every
    country, so the detail/edit screens those link into can't stay locked
    to whichever country happens to be active. Every other role stays scoped to it, same as
    before. Shared across apps (tasks, customers) rather than duplicated,
    since the rule is identical regardless of what's being looked up.
    """
    if not requesting_technician.is_manager_tier:
        queryset = queryset.filter(**{country_lookup: active_country})
    return get_object_or_404(queryset, pk=pk)


def require_admin(request):
    """Restrict a view to admins only — used solely for Roles &
    permissions. Deliberately not satisfied by a plain manager: if that
    screen were reachable by the same tier it configures, a bad edit
    there could lock every role out of ever fixing it again.
    """
    technician = getattr(request.user, 'technician', None)
    if technician is None or technician.role != Technician.Role.ADMIN:
        raise PermissionDenied
    return technician


def can_manage_account(actor, target):
    """Only an admin can change an admin's account, and only the manager
    tier a manager's — otherwise someone below could reset a password or
    change an email and sign in as the person above them.
    """
    if target.role == Technician.Role.ADMIN:
        return actor.role == Technician.Role.ADMIN
    if target.is_manager_tier:
        return actor.is_manager_tier
    return True


def is_last_active_admin(technician):
    """The one admin left who can still sign in — never turned off,
    deactivated or moved to another role, or nobody could reach Roles &
    permissions or this screen's admin rows again.
    """
    return technician.role == Technician.Role.ADMIN and not Technician.objects.filter(
        role=Technician.Role.ADMIN, is_active=True, user__is_active=True,
    ).exclude(pk=technician.pk).exists()
