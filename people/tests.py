from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from reference.models import Country

from .models import RolePermission, Technician

User = get_user_model()


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


@override_settings(ALLOWED_HOSTS=['testserver'], AXES_ENABLED=True)
class LoginLockoutTests(TestCase):
    """django-axes locks an IP out after repeated failed logins — every
    username here is predictable (supervisor1, manager1, ...), so
    nothing else in the app throttles guessing. AXES_ENABLED is off by
    default under the test runner (see settings.py) since Client.login()
    doesn't supply the request object AxesStandaloneBackend requires —
    turned back on here so this class can actually exercise it.
    """

    def setUp(self):
        country = Country.objects.create(
            name='UAE', iso_code='AE', timezone='Asia/Dubai', currency_code='AED',
        )
        self.user = User.objects.create_user('locktest', password='correct-horse-battery')
        Technician.objects.create(
            user=self.user, country=country, full_name='Lock Test',
            language='en', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )

    def test_correct_password_still_works_under_the_limit(self):
        response = self.client.post('/accounts/login/', {'username': 'locktest', 'password': 'correct-horse-battery'})
        self.assertEqual(response.status_code, 302)

    def test_locks_out_after_repeated_failures(self):
        for _ in range(5):
            self.client.post('/accounts/login/', {'username': 'locktest', 'password': 'wrong'})

        # Even the correct password is now rejected — the account itself
        # was never compromised, but this IP is locked out regardless.
        response = self.client.post(
            '/accounts/login/', {'username': 'locktest', 'password': 'correct-horse-battery'},
        )
        self.assertEqual(response.status_code, 429)

    def test_a_different_ip_is_not_affected_by_another_ips_lockout(self):
        for _ in range(5):
            self.client.post(
                '/accounts/login/', {'username': 'locktest', 'password': 'wrong'},
                REMOTE_ADDR='10.0.0.1',
            )
        response = self.client.post(
            '/accounts/login/', {'username': 'locktest', 'password': 'correct-horse-battery'},
            REMOTE_ADDR='10.0.0.2',
        )
        self.assertEqual(response.status_code, 302)
