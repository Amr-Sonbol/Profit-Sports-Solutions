from django.test import TestCase

from .models import RolePermission


class SeedRolePermissionsTests(TestCase):
    """Locks in the data migration in 0006_seed_role_permissions.py — it
    must reproduce exactly what require_supervisor used to hardcode.
    """

    def test_matches_old_hardcoded_supervisor_check(self):
        expected_allowed_roles = {'supervisor', 'manager'}
        for permission in RolePermission.Permission:
            allowed_roles = set(
                RolePermission.objects.filter(permission=permission, allowed=True).values_list('role', flat=True),
            )
            self.assertEqual(allowed_roles, expected_allowed_roles, permission)

    def test_every_role_has_a_row_for_every_permission(self):
        self.assertEqual(
            RolePermission.objects.count(),
            len(RolePermission.Permission) * 3,
        )
