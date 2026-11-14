from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils import timezone

from customers.models import PortalUser
from people.models import Technician
from reports.models import CustomerFeedback, WorkReport
from tasks.models import CustomerTicket, Task, TaskAssignment
from tasks.tests import TaskTestCase

User = get_user_model()

# Covered elsewhere: the Django admin, the token-authenticated mobile API
# (api/tests.py), and the password-reset confirm link (its uidb64/token
# can't be faked here — Django's own views).
SKIPPED_PREFIXES = ('admin/', 'api/', 'accounts/reset/')


def _all_page_routes():
    """Every (route, url name) in the URLconf, walked recursively — so a
    page added later is smoke-tested without anyone listing it here."""
    def walk(patterns, prefix='', namespace=''):
        for pattern in patterns:
            route = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                ns = pattern.namespace or namespace
                yield from walk(pattern.url_patterns, route, ns)
            elif isinstance(pattern, URLPattern) and pattern.name:
                name = f'{namespace}:{pattern.name}' if namespace else pattern.name
                yield route, name
    return [(r, n) for r, n in walk(get_resolver().url_patterns) if not r.startswith(SKIPPED_PREFIXES)]


@override_settings(AXES_ENABLED=False)
class PageSmokeTests(TaskTestCase):
    """Open every page in the web app as every kind of user and check none
    of them crashes (a 500). Refusals (403/404) and redirects are fine —
    whether each role is *allowed* in is checked by the feature tests;
    this only guarantees nothing blows up before production."""

    def setUp(self):
        super().setUp()
        self.task = Task.objects.create(
            task_number='AE-0001', site=self.site, task_type=self.task_type, brand=self.brand,
            priority=Task.Priority.NORMAL, source=Task.Source.PHONE, billing_type=Task.BillingType.CHARGEABLE,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.IN_PROGRESS,
        )
        TaskAssignment.objects.create(
            task=self.task, technician=self.technician, role=TaskAssignment.Role.LEAD,
            assigned_at=timezone.now(), is_active=True,
        )
        WorkReport.objects.create(
            task=self.task, findings='Belt worn', action_taken='Replaced belt', resolved=True,
            labour_hours='1.50', customer_name='Ali', submitted_at=timezone.now(),
        )
        self.ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0001', company_name='Fitness First',
            site_description='Marina Branch', contact_name='Ali', contact_phone='0501234567',
            description='Treadmill belt squeaking.', submitted_at=timezone.now(),
        )
        self.feedback = CustomerFeedback.objects.create(
            task=self.task, requested_at=timezone.now(), requested_by=self.supervisor_user,
        )
        self.portal_user = User.objects.create_user('portal1', password='pass12345')
        PortalUser.objects.create(customer=self.customer, user=self.portal_user)

    def _kwargs_for(self, route, name):
        """Real objects for each URL's placeholders."""
        kwargs = {}
        if '<int:pk>' in route:
            if name.startswith('customers:site_'):
                kwargs['pk'] = self.site.pk
            elif name.startswith('customers:'):
                kwargs['pk'] = self.customer.pk
            elif name.startswith('tasks:technician_'):
                kwargs['pk'] = self.technician.pk
            elif name == 'tasks:machine_detail':
                kwargs['pk'] = self.asset.pk
            elif name == 'tasks:ticket_review':
                kwargs['pk'] = self.ticket.pk
            else:
                kwargs['pk'] = self.task.pk
        if '<str:token>' in route:
            kwargs['token'] = self.feedback.token if name.startswith('reports:') else self.ticket.token
        return kwargs

    def _users(self):
        users = [('signed out', None), ('customer', 'portal1')]
        for role in Technician.Role:
            username = f'smoke_{role.value}'
            user = User.objects.create_user(username, password='pass12345')
            Technician.objects.create(
                user=user, country=self.country, full_name=f'Smoke {role.label}',
                language='en', role=role, employment_type='staff',
            )
            users.append((role.label, username))
        users.append(('technician, Arabic', 'tech1'))
        return users

    def test_no_page_crashes_for_anyone(self):
        from django.urls import reverse

        routes = _all_page_routes()
        self.assertGreater(len(routes), 50)
        for who, username in self._users():
            self.client.logout()
            if username:
                self.client.login(username=username, password='pass12345')
            for route, name in routes:
                url = reverse(name, kwargs=self._kwargs_for(route, name))
                with self.subTest(user=who, url=url):
                    response = self.client.get(url)
                    self.assertLess(response.status_code, 500)


class StaffKeepMeSignedInTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_user('staffer', password='pass12345')

    def test_ticked_keeps_the_sign_in_and_unticked_ends_it_with_the_browser(self):
        for remember, ends_with_browser in (('1', False), ('', True)):
            with self.subTest(remember=remember):
                client = Client()
                client.post('/accounts/login/', {'username': 'staffer', 'password': 'pass12345', 'remember': remember})
                self.assertIn('_auth_user_id', client.session)
                self.assertEqual(client.session.get_expire_at_browser_close(), ends_with_browser)

    def test_the_box_is_ticked_to_begin_with(self):
        self.assertContains(Client().get('/accounts/login/'), 'name="remember" value="1" checked')


class BrowserProtectionTests(TestCase):
    """The Content-Security-Policy (spots.middleware) on every page, and the
    staff "Forgot your password?" limited like the portal's (spots.throttle)."""

    def test_every_page_says_where_scripts_may_come_from(self):
        response = self.client.get('/accounts/login/')
        policy = response['Content-Security-Policy']
        nonce = response.wsgi_request.csp_nonce
        self.assertIn(f"script-src 'self' 'nonce-{nonce}' https://unpkg.com", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        # The page's own inline script carries it; nothing uses inline event attributes.
        self.assertContains(response, f'<script nonce="{nonce}">')
        self.assertNotContains(response, 'onclick=')
        self.assertNotContains(response, 'onchange=')

    def test_a_new_nonce_every_request(self):
        first = self.client.get('/accounts/login/').wsgi_request.csp_nonce
        self.assertNotEqual(first, self.client.get('/accounts/login/').wsgi_request.csp_nonce)

    def test_staff_password_reset_sends_one_link_in_a_while(self):
        from django.core import mail

        get_user_model().objects.create_user('sara', email='sara@example.com', password='pass12345')
        for _ in range(3):
            self.client.post('/accounts/password-reset/', {'email': 'sara@example.com'})
        self.assertEqual(len(mail.outbox), 1)


class UploadSafetyTests(TestCase):
    """Every upload is what its name says (spots.uploads), and an uploaded file
    can never act as a page of this site (spots.media.serve_media)."""

    def check(self, name, content):
        from django.core.exceptions import ValidationError
        from django.core.files.uploadedfile import SimpleUploadedFile

        from spots.uploads import SafeFileValidator
        try:
            SafeFileValidator(allowed_extensions=['jpg', 'png', 'pdf', 'mp4', 'webm', 'heic'])(
                SimpleUploadedFile(name, content))
            return True
        except ValidationError:
            return False

    def test_real_files_pass_and_disguised_ones_do_not(self):
        from spots.test_files import REAL_JPEG, REAL_MP4, REAL_PDF, REAL_PNG
        self.assertTrue(self.check('photo.jpg', REAL_JPEG))
        self.assertTrue(self.check('sign.png', REAL_PNG))
        self.assertTrue(self.check('report.pdf', REAL_PDF))
        self.assertTrue(self.check('noise.mp4', REAL_MP4))
        self.assertFalse(self.check('photo.jpg', b'<html><script>alert(1)</script></html>'))  # a page, renamed
        self.assertTrue(self.check('photo.jpg', REAL_PNG))  # a real screenshot a phone app named .jpg: still a photo
        self.assertFalse(self.check('report.pdf', b'MZ not a pdf, a program'))
        self.assertFalse(self.check('noise.mp4', b'not a video at all'))
        self.assertFalse(self.check('tool.exe', b'MZ'))  # not an allowed kind at all

    def test_uploaded_files_run_nothing_and_others_download(self):
        import os
        import tempfile

        from django.test import override_settings
        from spots.test_files import REAL_JPEG, REAL_PDF

        media = tempfile.mkdtemp()
        for name, content in (('a.jpg', REAL_JPEG), ('b.pdf', REAL_PDF), ('c.csv', b'a,b')):
            with open(os.path.join(media, name), 'wb') as handle:
                handle.write(content)
        staff = get_user_model().objects.create_user('staff1', password='pass12345')
        from reference.models import Country
        Technician.objects.create(user=staff, country=Country.objects.create(
            name='UAE', iso_code='AE', timezone='Asia/Dubai', currency_code='AED'),
            full_name='Staff', language='en', role=Technician.Role.SUPERVISOR, employment_type='staff')
        self.client.force_login(staff)
        with override_settings(MEDIA_ROOT=media):
            photo, pdf, other = (self.client.get(f'/media/{name}') for name in ('a.jpg', 'b.pdf', 'c.csv'))
        self.assertIn('sandbox', photo['Content-Security-Policy'])
        self.assertFalse(photo.get('Content-Disposition', '').startswith('attachment'))  # shown in the browser
        self.assertIn("default-src 'none'", pdf['Content-Security-Policy'])
        self.assertNotIn('sandbox', pdf['Content-Security-Policy'])  # the browser's PDF viewer runs
        self.assertEqual(other['Content-Disposition'], 'attachment')
