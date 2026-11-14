from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from reference.models import Country

from .models import RolePermission, Technician

ACTIVE_COUNTRY_SESSION_KEY = 'active_country_id'


def allowed_permissions(request, technician):
    """The permissions `technician`'s role has, read once per request (the page's
    own check, its menu and its bell all ask) — never kept longer, so a change on
    the Roles & permissions screen applies from the very next page."""
    cached = getattr(request, '_allowed_permissions', None)
    if cached is None or cached[0] != technician.permission_role:
        cached = (technician.permission_role, frozenset(
            RolePermission.objects.filter(role=technician.permission_role, allowed=True).values_list('permission', flat=True),
        ))
        request._allowed_permissions = cached
    return cached[1]


def require_permission(request, permission):
    """Restrict a view to technicians whose role currently has this
    permission enabled. Configurable per role from the Roles & permissions
    screen — nothing here is hardcoded to a specific role.
    """
    technician = getattr(request.user, 'technician', None)
    if technician is None:
        raise PermissionDenied
    if permission not in allowed_permissions(request, technician):
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
    if technician.reaches_all_countries:
        active_id = request.session.get(ACTIVE_COUNTRY_SESSION_KEY)
        if active_id:
            country = Country.objects.filter(pk=active_id).first()
            if country is not None:
                return country
    return technician.country


def require_all_countries(request):
    """The every-country screens (all-countries boards, switching country, the
    company-wide report, the shared lists): the Project, Technical Support and
    Operations Managers and Admin — not a Manager, who has one country
    (docs/access_rules.md)."""
    technician = getattr(request.user, 'technician', None)
    if technician is None or not technician.reaches_all_countries:
        raise PermissionDenied
    return technician


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


# The work that touches the warehouse — all a Warehouse Manager sees of tasks (docs/access_rules.md).
STOCK_TASK_CATEGORIES = ('part_shipment', 'receiving', 'loading', 'installation')


def tasks_for_role(technician, tasks):
    """A task list as this role sees it: a Warehouse Manager, only stock-related
    tasks — and repairs that need parts, to prepare them (tasks.parts_needed);
    everyone else, as their country (or every country) allows."""
    if technician.role == technician.Role.WAREHOUSE_MANAGER:
        from django.db.models import Q
        return tasks.filter(Q(task_type__category__in=STOCK_TASK_CATEGORIES) | Q(parts_needed__isnull=False)).distinct()
    return tasks


def scoped_or_404(queryset, pk, requesting_technician, active_country, country_lookup):
    """A manager can open any task, technician, or customer regardless of
    their own active country — the point of the all-countries task list
    and the all_technicians board and Customers' All countries is reaching across every
    country, so the detail/edit screens those link into can't stay locked
    to whichever country happens to be active. Every other role stays scoped to it, same as
    before. Shared across apps (tasks, customers) rather than duplicated,
    since the rule is identical regardless of what's being looked up.
    """
    if not requesting_technician.reaches_all_countries:
        queryset = queryset.filter(**{country_lookup: active_country})
    if queryset.model._meta.label == 'tasks.Task':
        queryset = tasks_for_role(requesting_technician, queryset)
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


# Whose logins each rank manages on Staff accounts (unlock, sign out phones,
# turn off/on, setup link): never someone above or beside them.
FIELD_AND_DESK_ROLES = (
    Technician.Role.SUPERVISOR, Technician.Role.TECHNICIAN, Technician.Role.WAREHOUSE_MANAGER,
    Technician.Role.COUNTRY_SUPPORT,
)
SENIOR_MANAGERS = (Technician.Role.MANAGER, Technician.Role.PROJECT_MANAGER, Technician.Role.SUPPORT_MANAGER)


def can_manage_account(actor, target):
    """Whether `actor` may act on `target`'s login (docs/access_rules.md):
    the Admin anyone's; the COO anyone's but the Admin's; the Operations
    Manager anyone's but the Admin's and the COO's; the Manager, Project and
    Technical Support Manager only the field and desk roles', in their own
    country unless they reach every country. Nobody else, and never through
    a role made on Roles & permissions (only its switches)."""
    if actor.custom_role_id:
        return False
    if actor.role == Technician.Role.ADMIN:
        return True
    if target.role == Technician.Role.ADMIN:
        return False
    if actor.role == Technician.Role.COO:
        return True
    if actor.role == Technician.Role.OPERATIONS_MANAGER:
        return target.role != Technician.Role.COO
    if actor.role in SENIOR_MANAGERS and target.role in FIELD_AND_DESK_ROLES:
        return actor.reaches_all_countries or actor.country_id == target.country_id
    return False


# Who creates a customer or a branch — from a request, the Customers page or
# while planning an installation (docs/access_rules.md). Editing them is the
# "edit customers" switch.
CUSTOMER_CREATORS = (
    Technician.Role.MANAGER, Technician.Role.PROJECT_MANAGER, Technician.Role.SUPPORT_MANAGER,
    Technician.Role.OPERATIONS_MANAGER, Technician.Role.COO, Technician.Role.ADMIN,
)


def can_create_customers(technician):
    """Creates customers and branches: the roles in CUSTOMER_CREATORS (built-in)."""
    return not technician.custom_role_id and technician.role in CUSTOMER_CREATORS


# Who plans a loading's or an installation's machines, and sends loaded machines on
# to an installation (docs/access_rules.md): everyone else views. A role made later
# (a Coordinator, say) views too unless it is one of these.
EQUIPMENT_PLANNERS = (Technician.Role.MANAGER, Technician.Role.PROJECT_MANAGER, Technician.Role.ADMIN)


def can_plan_equipment(technician):
    """Plans the machines on a loading or an installation, and sends loaded
    machines on: the Manager, the Project Manager and the Admin (built-in roles only)."""
    return not technician.custom_role_id and technician.role in EQUIPMENT_PLANNERS


def can_set_password(actor):
    """Only an admin sets someone else's password (a temporary one, or one
    typed in) or changes their login email: whoever did either could sign
    in as them. Everyone else sends the person a link to their own email
    (a setup or password link), and the person chooses their own."""
    return actor.role == Technician.Role.ADMIN


def is_last_active_admin(technician):
    """The one admin left who can still sign in — never turned off,
    deactivated or moved to another role, or nobody could reach Roles &
    permissions or this screen's admin rows again.
    """
    return technician.role == Technician.Role.ADMIN and not Technician.objects.filter(
        role=Technician.Role.ADMIN, is_active=True, user__is_active=True,
    ).exclude(pk=technician.pk).exists()
