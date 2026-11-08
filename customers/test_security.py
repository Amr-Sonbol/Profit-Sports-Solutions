"""Security checks, as an outsider or a nosy customer would try them: one
customer reaching another's requests, visits, reports and machines by
changing a number or token in the address; customers and strangers on staff
pages; the portal's own address; an admin's "View as customer" changing
nothing; uploads and typed text that could run as script. Run before a
release:

    python manage.py test customers.test_security
"""
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from customers.models import Asset, Customer, Site
from people.models import Technician
from reference.models import Brand, Country
from reports.models import CustomerFeedback, WorkReport
from tasks.models import CustomerTicket, Task

User = get_user_model()
PASSWORD = 'pass12345'


class SecurityTestCase(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name='UAE', iso_code='AE', timezone='Asia/Dubai', currency_code='AED')
        admin_user = User.objects.create_user('admin1', password=PASSWORD)
        self.admin = Technician.objects.create(
            user=admin_user, country=self.country, full_name='Amina Admin', language='en',
            role=Technician.Role.ADMIN, employment_type='staff',
        )
        self.brand = Brand.objects.create(name='Technogym')
        self.a = self._customer('gyma', 'Gym A')
        self.b = self._customer('gymb', 'Gym B')

    def _customer(self, username, name):
        user = User.objects.create_user(username, password=PASSWORD)
        customer = Customer.objects.create(country=self.country, name=name, segment='gym', user=user)
        site = Site.objects.create(customer=customer, name=f'{name} Main', address='Dubai')
        asset = Asset.objects.create(site=site, brand=self.brand, model_name='Excite Run 700', serial_no=f'{username}-1')
        task = Task.objects.create(
            task_number=f'AE-{username}', site=site, priority=Task.Priority.NORMAL, source=Task.Source.PORTAL,
            billing_type=Task.BillingType.CHARGEABLE, reported_at=timezone.now(), created_by=user,
            status=Task.Status.CLOSED,
        )
        WorkReport.objects.create(task=task, findings='Private findings of ' + name, action_taken='-', resolved=True,
                                  labour_hours='1', customer_name='-', submitted_at=timezone.now())
        ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number=f'AE-T-{username}', customer=customer, site=site,
            company_name=name, contact_name='-', contact_phone='-', description='Private request of ' + name,
            submitted_at=timezone.now(), task=task, status=CustomerTicket.Status.CONVERTED,
        )
        feedback = CustomerFeedback.objects.create(task=task, requested_at=timezone.now(), requested_by=user)
        return type('Fixture', (), dict(user=user, customer=customer, site=site, asset=asset, task=task,
                                        ticket=ticket, feedback=feedback, username=username))

    def login(self, username, host='testserver'):
        client = self.client_class(HTTP_HOST=host)
        client.login(username=username, password=PASSWORD)
        return client


class CustomersStayInTheirOwnDataTests(SecurityTestCase):
    def test_another_customers_pages_are_not_found(self):
        a = self.login('gyma')
        b = self.b
        for url in (
            f'/tasks/tickets/status/{b.ticket.token}/',
            f'/customers/portal/visits/{b.task.pk}/',
            f'/customers/portal/visits/{b.task.pk}/report.pdf',
            f'/customers/portal/machines/{b.asset.pk}/',
        ):
            with self.subTest(url=url):
                # Refused, or (a request's emailed link) sent to this account's own requests.
                response = a.get(url, follow=True)
                self.assertIn(response.status_code, (200, 403, 404), url)
                if response.status_code == 200:
                    self.assertEqual(response.redirect_chain[-1][0], '/customers/portal/requests/')
                self.assertNotIn(b'Private', response.content)

    def test_the_rating_link_from_an_email_shows_nothing_private(self):
        # The rating page opens from its emailed link (a long random token), like any "rate your visit" link;
        # it must never show the visit's report or the request.
        response = self.login('gyma').get(f'/reports/feedback/{self.b.feedback.token}/')
        self.assertNotIn(b'Private', response.content)

    def test_their_own_pages_open(self):
        a = self.login('gyma')
        for url in (f'/tasks/tickets/status/{self.a.ticket.token}/', f'/customers/portal/visits/{self.a.task.pk}/',
                    f'/customers/portal/machines/{self.a.asset.pk}/'):
            with self.subTest(url=url):
                self.assertEqual(a.get(url).status_code, 200)

    def test_lists_show_only_their_own(self):
        a = self.login('gyma')
        for url in ('/customers/portal/', '/customers/portal/requests/?show=closed', '/customers/portal/visits/?show=past',
                    '/customers/portal/reports/', '/customers/portal/machines/', '/customers/portal/settings/'):
            with self.subTest(url=url):
                response = a.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'Gym B')
                self.assertNotContains(response, 'gymb-1')

    def test_a_request_for_another_customers_branch_is_refused(self):
        a = self.login('gyma')
        a.post('/customers/portal/tickets/new/', {
            'site': self.b.site.pk, 'contact_name': 'X', 'contact_phone': '1', 'about': 'other', 'need': 'assistance',
            'subject': 'x', 'message': 'x', 'machines-TOTAL_FORMS': '0', 'machines-INITIAL_FORMS': '0',
            'machines-MIN_NUM_FORMS': '0', 'machines-MAX_NUM_FORMS': '10',
        })
        self.assertFalse(CustomerTicket.objects.filter(site=self.b.site).exclude(pk=self.b.ticket.pk).exists())

    def test_replying_on_another_customers_request_does_nothing(self):
        a = self.login('gyma')
        a.post(f'/tasks/tickets/status/{self.b.ticket.token}/', {'message': 'Injected reply'})
        self.assertFalse(self.b.ticket.replies.filter(message='Injected reply').exists())


class StaffPagesAreClosedToOthersTests(SecurityTestCase):
    STAFF_PAGES = ('/tasks/', '/tasks/tickets/', '/customers/', '/tasks/technicians/', '/tasks/reports/monthly/',
                   '/customers/accounts/', '/admin/')

    def test_a_customer_cannot_open_staff_pages(self):
        a = self.login('gyma')
        for url in self.STAFF_PAGES + (f'/tasks/{self.a.task.pk}/', f'/tasks/tickets/{self.a.ticket.pk}/'):
            with self.subTest(url=url):
                response = a.get(url)
                self.assertNotEqual(response.status_code, 200, url)
                self.assertNotIn(b'Gym B', response.content)

    def test_a_stranger_is_sent_to_sign_in(self):
        stranger = self.client_class()
        for url in self.STAFF_PAGES + ('/customers/portal/', '/customers/portal/requests/'):
            with self.subTest(url=url):
                self.assertIn(stranger.get(url).status_code, (302, 403, 404))
        self.assertIn(stranger.get(f'/tasks/tickets/status/{self.a.ticket.token}/').status_code, (302, 403, 404))

    def test_the_app_api_refuses_customers_and_strangers(self):
        for client in (self.client_class(), self.login('gyma')):
            with self.subTest(client=client):
                self.assertIn(client.get('/api/my-tasks/').status_code, (401, 403))


@override_settings(PORTAL_HOST='portal.example.test', ALLOWED_HOSTS=['testserver', 'portal.example.test'])
class PortalAddressTests(SecurityTestCase):
    def test_no_staff_page_exists_on_the_portal_address(self):
        admin = self.login('admin1', host='portal.example.test')
        for url in StaffPagesAreClosedToOthersTests.STAFF_PAGES + ('/api/my-tasks/',):
            with self.subTest(url=url):
                self.assertEqual(admin.get(url).status_code, 404)

    def test_signing_out_on_the_portal_lands_on_the_portal_sign_in(self):
        customer = self.login('gyma', host='portal.example.test')
        response = customer.post('/accounts/logout/', follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[-1][0], '/customers/portal/login/')
        self.assertNotIn('_auth_user_id', customer.session)

    def test_a_customer_sign_in_left_on_the_staff_address_is_ended(self):
        customer = self.login('gyma')
        response = customer.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('_auth_user_id', customer.session)
        self.assertIn('/accounts/login/', customer.get('/').url)

    def test_a_customer_on_the_staff_sign_in_is_pointed_to_the_portal(self):
        page = self.client_class().post('/accounts/login/', {'username': 'gyma', 'password': PASSWORD})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Customers sign in at portal.example.test')

    def test_staff_still_sign_in_on_the_staff_address(self):
        response = self.client_class().post('/accounts/login/', {'username': 'admin1', 'password': PASSWORD})
        self.assertEqual(response.status_code, 302)

    @override_settings(STAFF_HOST='staff.example.test')
    def test_a_staff_link_sent_from_the_portal_points_to_the_staff_site(self):
        from django.test import RequestFactory
        from spots.hosts import staff_url
        on_portal = RequestFactory(HTTP_HOST='portal.example.test').get('/')
        on_staff = RequestFactory().get('/')
        self.assertEqual(staff_url(on_portal, '/tasks/tickets/7/'), 'http://staff.example.test/tasks/tickets/7/')
        self.assertEqual(staff_url(on_staff, '/tasks/tickets/7/'), 'http://testserver/tasks/tickets/7/')

    def test_portal_links_on_the_staff_address_move_to_the_portal(self):
        response = self.client_class().get('/customers/portal/login/')
        self.assertEqual(response['Location'], 'http://portal.example.test/customers/portal/login/')


class ViewAsChangesNothingTests(SecurityTestCase):
    def test_an_admin_viewing_as_a_customer_cannot_act_for_them(self):
        admin = self.login('admin1')
        admin.post(f'/customers/{self.a.customer.pk}/view-as/')
        self.assertEqual(admin.get('/customers/portal/').status_code, 200)
        before = CustomerTicket.objects.count()
        admin.post('/customers/portal/tickets/new/', {
            'site': self.a.site.pk, 'contact_name': 'X', 'contact_phone': '1', 'about': 'other', 'need': 'assistance',
            'subject': 'x', 'message': 'x', 'machines-TOTAL_FORMS': '0', 'machines-INITIAL_FORMS': '0',
            'machines-MIN_NUM_FORMS': '0', 'machines-MAX_NUM_FORMS': '10',
        })
        admin.post('/customers/portal/settings/', {'action': 'details', 'name': 'Hacked'})
        self.assertEqual(CustomerTicket.objects.count(), before)
        self.a.customer.refresh_from_db()
        self.assertEqual(self.a.customer.name, 'Gym A')


class UploadsAndTextTests(SecurityTestCase):
    def _send(self, client, files):
        return client.post('/customers/portal/tickets/new/', {
            'site': self.a.site.pk, 'contact_name': 'X', 'contact_phone': '1', 'about': 'other', 'need': 'assistance',
            'subject': 'x', 'message': 'x', 'machines-TOTAL_FORMS': '0', 'machines-INITIAL_FORMS': '0',
            'machines-MIN_NUM_FORMS': '0', 'machines-MAX_NUM_FORMS': '10', 'attachments': files,
        })

    def test_files_that_could_run_as_script_are_refused(self):
        a = self.login('gyma')
        before = CustomerTicket.objects.count()
        for name, content in (('evil.html', b'<script>alert(1)</script>'), ('evil.svg', b'<svg onload="alert(1)"/>'),
                              ('tool.exe', b'MZ'), ('page.js', b'alert(1)')):
            with self.subTest(name=name):
                self._send(a, [SimpleUploadedFile(name, content)])
                self.assertEqual(CustomerTicket.objects.count(), before)

    def test_typed_script_is_shown_as_text(self):
        a = self.login('gyma')
        response = a.post('/customers/portal/tickets/new/', {
            'site': self.a.site.pk, 'contact_name': '<script>alert("n")</script>', 'contact_phone': '1',
            'about': 'other', 'need': 'assistance', 'subject': '<img src=x onerror=alert(1)>',
            'message': '<script>alert("m")</script>', 'machines-TOTAL_FORMS': '0', 'machines-INITIAL_FORMS': '0',
            'machines-MIN_NUM_FORMS': '0', 'machines-MAX_NUM_FORMS': '10',
        })
        self.assertEqual(response.status_code, 302)
        ticket = CustomerTicket.objects.order_by('-pk').first()
        admin = self.login('admin1')
        for url in (f'/tasks/tickets/{ticket.pk}/', '/tasks/tickets/', f'/tasks/tickets/status/{ticket.token}/'):
            page = (admin if url.startswith('/tasks/tickets/') and 'status' not in url else a).get(url).content
            with self.subTest(url=url):
                self.assertNotIn(b'<script>alert', page)
                self.assertNotIn(b'<img src=x', page)


class UploadedFilesTests(SecurityTestCase):
    """Files go through a check: staff see them, a customer only their own,
    the app by a signed link, nobody else."""

    def setUp(self):
        import tempfile
        self._media = override_settings(MEDIA_ROOT=tempfile.mkdtemp())
        self._media.enable()
        self.addCleanup(self._media.disable)
        super().setUp()
        from tasks.models import CustomerTicketAttachment, TicketInternalNote, TicketInternalNoteFile
        self.a_file = CustomerTicketAttachment.objects.create(
            ticket=self.a.ticket, file=SimpleUploadedFile('photo.jpg', b'A-photo', content_type='image/jpeg'),
            uploaded_at=timezone.now(),
        ).file
        self.b_file = CustomerTicketAttachment.objects.create(
            ticket=self.b.ticket, file=SimpleUploadedFile('photo.jpg', b'B-photo', content_type='image/jpeg'),
            uploaded_at=timezone.now(),
        ).file
        note = TicketInternalNote.objects.create(ticket=self.a.ticket, author=self.admin.user, message='-',
                                                 created_at=timezone.now())
        self.internal_file = TicketInternalNoteFile.objects.create(
            note=note, file=SimpleUploadedFile('memo.pdf', b'internal', content_type='application/pdf'),
        ).file

    def get(self, client, file, query=''):
        return client.get('/media/' + file.name + query)

    def test_a_stranger_gets_nothing(self):
        for file in (self.a_file, self.b_file, self.internal_file):
            self.assertEqual(self.get(self.client_class(), file).status_code, 404)

    def test_a_customer_gets_only_their_own_request_files(self):
        a = self.login('gyma')
        self.assertEqual(b''.join(self.get(a, self.a_file).streaming_content), b'A-photo')
        self.assertEqual(self.get(a, self.b_file).status_code, 404)
        self.assertEqual(self.get(a, self.internal_file).status_code, 404)

    def test_staff_get_them(self):
        admin = self.login('admin1')
        for file in (self.a_file, self.b_file, self.internal_file):
            self.assertEqual(self.get(admin, file).status_code, 200)

    def test_the_apps_signed_link_opens_without_signing_in_and_cannot_be_reused_for_another_file(self):
        from django.test import RequestFactory
        from spots.media import signed_media_url
        link = signed_media_url(RequestFactory().get('/'), self.a_file.url)
        query = '?' + link.split('?', 1)[1]
        self.assertEqual(self.get(self.client_class(), self.a_file, query).status_code, 200)
        self.assertEqual(self.get(self.client_class(), self.b_file, query).status_code, 404)
        self.assertEqual(self.get(self.client_class(), self.a_file, '?t=forged').status_code, 404)

    def test_no_way_out_of_the_files_folder(self):
        self.assertEqual(self.login('admin1').get('/media/../spots/settings.py').status_code, 404)


class CopyWelcomeLinkTests(SecurityTestCase):
    """The welcome email's link, shown to staff to send another way."""

    def setUp(self):
        super().setUp()
        self.new = Customer.objects.create(country=self.country, name='New Gym', segment='gym',
                                           contact_email='owner@newgym.example')
        Site.objects.create(customer=self.new, name='New Gym Main', address='Dubai')

    def copy(self, client):
        return client.post('/customers/accounts/', {'action': 'copy_welcome_link', 'customer': self.new.pk})

    def test_an_admin_gets_the_link_and_it_opens_the_account_set_up(self):
        page = self.copy(self.login('admin1'))
        self.assertEqual(page.status_code, 200)
        link = page.context['link']
        self.assertIn('/customers/portal/account/', link)
        self.assertContains(page, link)
        self.new.refresh_from_db()
        self.assertEqual(self.new.user.username, 'owner@newgym.example')
        self.assertTrue(self.new.must_change_password)
        # It opens the account set-up page, exactly as the email's link does.
        self.assertTemplateUsed(self.client_class().get(link.split('testserver', 1)[1]), 'customers/portal_account_link.html')

    def test_a_new_link_replaces_the_one_before(self):
        admin = self.login('admin1')
        first = self.copy(admin).context['link']
        self.copy(admin)
        self.assertTemplateUsed(self.client_class().get(first.split('testserver', 1)[1]), 'customers/portal_link_invalid.html')

    def test_customers_cannot_get_it(self):
        self.assertNotEqual(self.copy(self.login('gyma')).status_code, 200)
        self.assertIsNone(Customer.objects.get(pk=self.new.pk).user)

    def test_a_temporary_password_in_one_click_for_a_customer_with_no_login_yet(self):
        page = self.login('admin1').post('/customers/accounts/', {'action': 'temporary_password', 'customer': self.new.pk})
        self.assertEqual(page.status_code, 200)
        password = page.context['password']
        self.new.refresh_from_db()
        self.assertTrue(self.new.must_change_password)
        self.assertTrue(self.client_class().login(username='owner@newgym.example', password=password))

    def test_no_login_is_made_for_a_customer_without_an_email(self):
        self.new.contact_email = ''
        self.new.save(update_fields=['contact_email'])
        self.login('admin1').post('/customers/accounts/', {'action': 'temporary_password', 'customer': self.new.pk})
        self.new.refresh_from_db()
        self.assertIsNone(self.new.user)


class KeepMeSignedInTests(SecurityTestCase):
    def test_ticked_keeps_the_sign_in_and_unticked_ends_it_with_the_browser(self):
        for remember, ends_with_browser in (('1', False), ('', True)):
            with self.subTest(remember=remember):
                client = self.client_class()
                client.post('/customers/portal/login/', {'username': 'gyma', 'password': PASSWORD, 'remember': remember})
                self.assertIn('_auth_user_id', client.session)
                self.assertEqual(client.session.get_expire_at_browser_close(), ends_with_browser)

    def test_the_box_is_ticked_to_begin_with(self):
        self.assertContains(self.client_class().get('/customers/portal/login/'), 'name="remember" value="1" checked')
