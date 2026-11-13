"""The access matrix: every page and every app endpoint, opened by every role
(and a customer, and someone signed out), against the expected result kept in
tasks/access_matrix.json — reviewed against docs/access_rules.md.

A change to who can open what fails here until it's deliberately approved:
rewrite the expected file and review its diff,

    ACCESS_MATRIX_WRITE=1 python manage.py test tasks.test_access_matrix

Opening a page (GET) is what's checked here; what each page's buttons do is
checked by the role's own rules (tasks.test_access and the area tests), and a
change of data is refused on the server whatever the page shows.
"""
import json
import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.authtoken.models import Token

from customers.models import Asset, Customer, PortalUser, Site
from people.models import Technician
from reference.models import Country
from tasks.models import CustomerTicket, Task, TaskAssignment
from tasks.tests import TaskTestCase

User = get_user_model()
EXPECTED = Path(__file__).with_name('access_matrix.json')

ROLES = [
    'admin', 'operations_manager', 'project_manager', 'support_manager', 'country_support', 'manager',
    'supervisor', 'warehouse_manager', 'technician',
]

# Every page a person opens (website), with the record it's about: task, other_task (another
# country's), ticket, other_ticket (another customer's), internal, person, customer, site,
# machine. Token pages (emailed links), sign-out and media are checked by their own tests.
WEB = [
    ('home', '/'), ('task_list', '/tasks/'), ('dashboard', '/tasks/dashboard/'), ('task_week', '/tasks/week/'),
    ('all_week', '/tasks/week/all/'), ('all_week_tasks', '/tasks/week/all/tasks/'), ('my_week', '/tasks/my-week/'),
    ('my_progress', '/tasks/my-progress/'), ('my_skills', '/tasks/my-skills/'), ('my_profile', '/tasks/my-profile/'),
    ('parts_to_prepare', '/tasks/parts-to-prepare/'), ('my_task_detail', '/tasks/my/{task}/'),
    ('my_report_form', '/tasks/my/{task}/report/'), ('task_create', '/tasks/new/'), ('ticket_form', '/tasks/tickets/new/'),
    ('ticket_list', '/tasks/tickets/'), ('ticket_open', '/tasks/tickets/open/'),
    ('internal_tickets', '/tasks/tickets/internal/'), ('internal_ticket_new', '/tasks/tickets/internal/new/'),
    ('internal_ticket', '/tasks/tickets/internal/{internal}/'), ('ticket_review', '/tasks/tickets/{ticket}/'),
    ('ticket_review_other_customer', '/tasks/tickets/{other_ticket}/'),
    ('technician_list', '/tasks/technicians/'), ('all_technicians', '/tasks/technicians/all/'),
    ('technician_hours', '/tasks/technicians/hours/'), ('technician_hours_export', '/tasks/technicians/hours/export/'),
    ('team_map', '/tasks/technicians/map/'), ('staff_accounts', '/tasks/technicians/accounts/'),
    ('technician_availability', '/tasks/technicians/availability/'), ('technician_create', '/tasks/technicians/new/'),
    ('technician_detail', '/tasks/technicians/{person}/'), ('technician_board', '/tasks/technicians/{person}/board/'),
    ('technician_progress', '/tasks/technicians/{person}/progress/'),
    ('technician_skills', '/tasks/technicians/{person}/skills/'), ('technician_edit', '/tasks/technicians/{person}/edit/'),
    ('view_as_user_start', '/tasks/technicians/{person}/view-as/'), ('team_progress', '/tasks/technicians/progress/'),
    ('machine_list', '/tasks/machines/'), ('machine_detail', '/tasks/machines/{machine}/'),
    ('role_permissions', '/tasks/roles/'), ('notification_settings', '/tasks/notifications/'),
    ('audit_log', '/tasks/audit-log/'), ('monthly_report', '/tasks/reports/monthly/'),
    ('monthly_report_export', '/tasks/reports/monthly/export/'), ('skill_list', '/tasks/skills/'),
    ('skill_create', '/tasks/skills/new/'), ('conduct_area_create', '/tasks/skills/conduct/new/'),
    ('brand_list', '/tasks/brands/'), ('brand_create', '/tasks/brands/new/'), ('part_list', '/tasks/parts/'),
    ('stock', '/tasks/stock/'), ('country_list', '/tasks/countries/'), ('country_create', '/tasks/countries/new/'),
    ('task_detail', '/tasks/{task}/'), ('task_detail_other_country', '/tasks/{other_task}/'),
    ('task_edit', '/tasks/{task}/edit/'), ('task_assign', '/tasks/{task}/assign/'),
    ('task_report_correct', '/tasks/{task}/report/correct/'), ('task_report_pdf', '/tasks/{task}/report.pdf'),
    ('customer_list', '/customers/'), ('customer_create', '/customers/new/'), ('customer_import', '/customers/import/'),
    ('customer_import_template', '/customers/import/template/'), ('customer_detail', '/customers/{customer}/'),
    ('customer_edit', '/customers/{customer}/edit/'), ('site_edit', '/customers/sites/{site}/edit/'),
    ('view_as_start', '/customers/{customer}/view-as/'),
    ('portal_home', '/customers/portal/'), ('portal_requests', '/customers/portal/requests/'),
    ('portal_visits', '/customers/portal/visits/'), ('portal_reports', '/customers/portal/reports/'),
    ('portal_machines', '/customers/portal/machines/'), ('portal_machine', '/customers/portal/machines/{machine}/'),
    ('portal_machine_other_customer', '/customers/portal/machines/{other_machine}/'),
    ('portal_visit', '/customers/portal/visits/{task}/'),
    ('portal_visit_other_customer', '/customers/portal/visits/{other_customer_task}/'),
    ('portal_ticket_new', '/customers/portal/tickets/new/'), ('portal_branch_request', '/customers/portal/branches/new/'),
    ('portal_machine_add', '/customers/portal/machines/add/'), ('portal_settings', '/customers/portal/settings/'),
]

# The app's endpoints (token sign-in).
API = [
    ('api_me', '/api/me/'), ('api_my_tasks', '/api/my-tasks/'), ('api_my_today', '/api/my-today/'),
    ('api_my_schedule', '/api/my-schedule/'), ('api_my_progress', '/api/my-progress/'), ('api_my_skills', '/api/my-skills/'),
    ('api_my_task_detail', '/api/my-tasks/{task}/'), ('api_my_task_report', '/api/my-tasks/{task}/report/'),
    ('api_tasks', '/api/tasks/?page=1'), ('api_task_filters', '/api/tasks/filters/'),
    ('api_team_task', '/api/tasks/{task}/'), ('api_team_task_other_country', '/api/tasks/{other_task}/'),
    ('api_task_lines', '/api/tasks/{task}/lines/'), ('api_task_messages', '/api/tasks/{task}/messages/'),
    ('api_task_candidates', '/api/tasks/{task}/candidates/'), ('api_task_edit', '/api/tasks/{task}/edit/'),
    ('api_task_manage', '/api/tasks/{task}/manage/'), ('api_tickets', '/api/tickets/?page=1'),
    ('api_ticket', '/api/tickets/{ticket}/'), ('api_ticket_other_customer', '/api/tickets/{other_ticket}/'),
    ('api_internal_tickets', '/api/internal-tickets/'), ('api_internal_ticket', '/api/internal-tickets/{internal}/'),
    ('api_customers', '/api/customers/'), ('api_customer', '/api/customers/{customer}/'),
    ('api_machines', '/api/machines/'), ('api_machine', '/api/machines/{machine}/'),
    ('api_dashboard', '/api/dashboard/'), ('api_team_today', '/api/team-today/'),
    ('api_team_progress', '/api/team-progress/'), ('api_team_map', '/api/team-map/'), ('api_waiting', '/api/waiting/'),
    ('api_bell', '/api/bell/'), ('api_hours', '/api/hours/'), ('api_parts', '/api/parts/'),
    ('api_parts_to_prepare', '/api/parts-to-prepare/'), ('api_staff_accounts', '/api/staff-accounts/'),
    ('api_person', '/api/people/{person}/'), ('api_website_menu', '/api/website-menu/'),
]


def outcome(response):
    """What a person meets: the page, refused, sent elsewhere (sign-in, their own side), not found."""
    code = response.status_code
    if code == 200:
        return 'open'
    if code in (301, 302):
        to = response.get('Location', '')
        if 'login' in to:
            return 'sign-in'
        if '/customers/portal' in to:
            return 'to-portal'
        return 'redirect'
    return {401: 'sign-in', 403: 'refused', 404: 'not-found', 405: 'post-only'}.get(code, str(code))


class AccessMatrixTests(TaskTestCase):
    maxDiff = None

    def setUp(self):
        super().setUp()
        now = timezone.now()
        # The records each page is about: ours in the UAE, another country's, another customer's.
        egypt = Country.objects.create(name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP')
        cairo = Site.objects.create(customer=Customer.objects.create(country=egypt, name='Cairo Gym', segment='gym'),
                                    name='Zamalek')
        other_customer = Customer.objects.create(country=self.country, name='Other Gym', segment='gym')
        other_site = Site.objects.create(customer=other_customer, name='Other Branch')
        task_kwargs = dict(task_type=self.task_type, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
                           billing_type=Task.BillingType.CHARGEABLE, reported_at=now, created_by=self.supervisor_user)
        self.task = Task.objects.create(task_number='UAE-0001', site=self.site,
                                        responsible_supervisor=self.supervisor_user.technician, **task_kwargs)
        TaskAssignment.objects.create(task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
                                      assigned_at=now, is_active=True)
        other_task = Task.objects.create(task_number='EG-0001', site=cairo, **task_kwargs)
        other_customer_task = Task.objects.create(task_number='UAE-0002', site=other_site, **task_kwargs)
        ticket_kwargs = dict(country=self.country, company_name='Fitness First', contact_name='Ali',
                             contact_phone='050', description='Stops', submitted_at=now)
        ticket = CustomerTicket.objects.create(ticket_number='UAE-T0001', customer=self.customer, site=self.site,
                                               **ticket_kwargs)
        other_ticket = CustomerTicket.objects.create(ticket_number='UAE-T0002', customer=other_customer,
                                                     site=other_site, **ticket_kwargs)
        internal = CustomerTicket.objects.create(
            ticket_number='UAE-T0003', kind=CustomerTicket.Kind.INTERNAL, subject='Order belts',
            assigned_to=self.supervisor_user.technician, **ticket_kwargs,
        )
        other_machine = Asset.objects.create(site=other_site, brand=self.brand, model_name='Bike')
        self.ids = {
            'task': self.task.pk, 'other_task': other_task.pk, 'other_customer_task': other_customer_task.pk,
            'ticket': ticket.pk, 'other_ticket': other_ticket.pk, 'internal': internal.pk,
            'person': self.technician.pk, 'customer': self.customer.pk, 'site': self.site.pk,
            'machine': self.asset.pk, 'other_machine': other_machine.pk,
        }
        # One person per role, in the UAE; the technician and the supervisor are the base case's own.
        self.users = {'technician': self.tech_user, 'supervisor': self.supervisor_user}
        for role in ROLES:
            if role not in self.users:
                user = User.objects.create_user(f'{role}1', password='pass12345')
                Technician.objects.create(user=user, country=self.country, full_name=role.title(), language='en',
                                          role=role, employment_type='staff')
                self.users[role] = user
        self.users['customer'] = User.objects.create_user('ali@fitnessfirst.example', password='pass12345')
        PortalUser.objects.create(customer=self.customer, user=self.users['customer'])

    def matrix(self):
        found = {}
        for who in ['anonymous', *ROLES, 'customer']:
            user = self.users.get(who)
            self.client.logout()
            if user is not None:
                self.client.force_login(user)
            for name, path in WEB:
                found.setdefault(name, {})[who] = outcome(self.client.get(path.format(**self.ids)))
            self.client.logout()
            headers = {}
            if user is not None:
                headers['HTTP_AUTHORIZATION'] = f'Token {Token.objects.get_or_create(user=user)[0].key}'
            for name, path in API:
                found.setdefault(name, {})[who] = outcome(self.client.get(path.format(**self.ids), **headers))
        return found

    def test_who_opens_what(self):
        found = self.matrix()
        if os.environ.get('ACCESS_MATRIX_WRITE'):
            EXPECTED.write_text(json.dumps(found, indent=1, sort_keys=False) + '\n', encoding='utf-8')
            self.skipTest(f'written to {EXPECTED.name}: review its diff')
        self.assertEqual(found, json.loads(EXPECTED.read_text(encoding='utf-8')))
