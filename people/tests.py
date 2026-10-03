from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from reference.models import Country

from .models import RolePermission, Technician

User = get_user_model()


class SeedRolePermissionsTests(TestCase):
    """Locks in the current shape of the RolePermission matrix — the
    original blanket supervisor+manager+admin seed (0006), narrowed twice
    for tickets (0021, 0023) and widened once for the dashboard (0024).
    """

    def test_most_permissions_match_old_hardcoded_supervisor_check(self):
        # manage_tickets, view_dashboard, view_machines, view_tasks,
        # view_technicians and decide_escalated_tickets are the permissions
        # that have since moved away from this default — each checked
        # separately below.
        expected_allowed_roles = {'supervisor', 'manager', 'admin'}
        other_permissions = set(RolePermission.Permission) - {
            RolePermission.Permission.MANAGE_TICKETS, RolePermission.Permission.VIEW_DASHBOARD,
            RolePermission.Permission.VIEW_MACHINES, RolePermission.Permission.VIEW_TASKS,
            RolePermission.Permission.VIEW_TECHNICIANS, RolePermission.Permission.DECIDE_ESCALATED_TICKETS,
        }
        for permission in other_permissions:
            allowed_roles = set(
                RolePermission.objects.filter(permission=permission, allowed=True).values_list('role', flat=True),
            )
            self.assertEqual(allowed_roles, expected_allowed_roles, permission)

    def test_view_tasks_also_includes_the_warehouse_manager(self):
        # The warehouse manager needs to search tasks by PAK reference and
        # open one to post a message — view_tasks alone covers both.
        allowed_roles = set(
            RolePermission.objects.filter(
                permission=RolePermission.Permission.VIEW_TASKS, allowed=True,
            ).values_list('role', flat=True),
        )
        self.assertEqual(allowed_roles, {'supervisor', 'manager', 'warehouse_manager', 'operations_manager', 'admin'})

    def test_manage_tickets_is_support_manager_and_admin_only(self):
        allowed_roles = set(
            RolePermission.objects.filter(
                permission=RolePermission.Permission.MANAGE_TICKETS, allowed=True,
            ).values_list('role', flat=True),
        )
        self.assertEqual(allowed_roles, {'support_manager', 'admin'})

    def test_view_dashboard_is_available_to_every_role(self):
        # The dashboard is now everyone's landing page after signing in
        # (spots.views.home) — every role needs to be able to see it.
        allowed_roles = set(
            RolePermission.objects.filter(
                permission=RolePermission.Permission.VIEW_DASHBOARD, allowed=True,
            ).values_list('role', flat=True),
        )
        self.assertEqual(allowed_roles, {r for r, _ in Technician.Role.choices})

    def test_view_machines_is_every_office_role_but_technician(self):
        allowed_roles = set(
            RolePermission.objects.filter(
                permission=RolePermission.Permission.VIEW_MACHINES, allowed=True,
            ).values_list('role', flat=True),
        )
        self.assertEqual(allowed_roles, {'supervisor', 'manager', 'support_manager', 'operations_manager', 'admin'})

    def test_view_technicians_also_includes_the_operations_manager(self):
        allowed_roles = set(
            RolePermission.objects.filter(
                permission=RolePermission.Permission.VIEW_TECHNICIANS, allowed=True,
            ).values_list('role', flat=True),
        )
        self.assertEqual(allowed_roles, {'supervisor', 'manager', 'operations_manager', 'admin'})

    def test_decide_escalated_tickets_is_operations_manager_and_admin(self):
        allowed_roles = set(
            RolePermission.objects.filter(
                permission=RolePermission.Permission.DECIDE_ESCALATED_TICKETS, allowed=True,
            ).values_list('role', flat=True),
        )
        self.assertEqual(allowed_roles, {'operations_manager', 'admin'})

    def test_every_role_has_a_row_for_every_permission(self):
        self.assertEqual(
            RolePermission.objects.count(),
            len(RolePermission.Permission) * len(Technician.Role.choices),
        )


class ActiveCountryContextTests(TestCase):
    """The header country switcher — manager-tier only (manager or admin),
    matching get_active_country's own is_manager_tier check. Found live:
    the context processor used to check role == MANAGER specifically,
    so admin — who the backend already lets switch — had no UI control
    to do it at all.
    """

    def setUp(self):
        self.country = Country.objects.create(
            name='UAE', iso_code='AE', timezone='Asia/Dubai', currency_code='AED',
        )
        for username, role in [
            ('tech1', Technician.Role.TECHNICIAN), ('supervisor1', Technician.Role.SUPERVISOR),
            ('manager1', Technician.Role.MANAGER), ('support1', Technician.Role.SUPPORT_MANAGER),
            ('admin1', Technician.Role.ADMIN),
        ]:
            user = User.objects.create_user(username, password='pass12345')
            Technician.objects.create(
                user=user, country=self.country, full_name=username,
                language='en', role=role, employment_type='staff',
            )

    def test_manager_and_admin_can_switch_countries(self):
        for username in ('manager1', 'admin1'):
            self.client.login(username=username, password='pass12345')
            response = self.client.get('/tasks/dashboard/')
            self.assertIn('switchable_countries', response.context, username)
            self.assertGreater(len(response.context['switchable_countries']), 0, username)
            self.client.logout()

    def test_everyone_else_gets_no_switcher(self):
        for username in ('tech1', 'supervisor1', 'support1'):
            self.client.login(username=username, password='pass12345')
            response = self.client.get('/tasks/dashboard/')
            self.assertNotIn('switchable_countries', response.context, username)
            self.client.logout()


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
