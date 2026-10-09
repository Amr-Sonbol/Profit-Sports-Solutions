from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from people.models import Technician
from reference.models import Country
from tasks.models import CustomerTicket, TicketNotification

from .models import Customer, Site

User = get_user_model()


class CustomerTestCase(TestCase):
    def setUp(self):
        self.country = Country.objects.create(
            name='UAE', iso_code='AE', timezone='Asia/Dubai', currency_code='AED',
        )
        self.supervisor_user = User.objects.create_user('supervisor1', password='pass12345')
        Technician.objects.create(
            user=self.supervisor_user, country=self.country, full_name='Sara Super',
            language='en', role=Technician.Role.SUPERVISOR, employment_type='staff',
        )
        self.tech_user = User.objects.create_user('tech1', password='pass12345')
        Technician.objects.create(
            user=self.tech_user, country=self.country, full_name='Tarek Tech',
            language='ar', role=Technician.Role.TECHNICIAN, employment_type='staff',
        )
        self.admin_user = User.objects.create_user('admin1', password='pass12345')
        Technician.objects.create(
            user=self.admin_user, country=self.country, full_name='Amina Admin',
            language='en', role=Technician.Role.ADMIN, employment_type='staff',
        )
        self.customer = Customer.objects.create(country=self.country, name='Fitness First', segment='gym')
        self.site = Site.objects.create(customer=self.customer, name='Marina Branch', address='Dubai Marina')


class CustomerListTests(CustomerTestCase):
    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/customers/')
        self.assertEqual(response.status_code, 403)

    def test_supervisor_sees_customers_in_own_country(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/customers/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fitness First')

    def test_other_country_customer_not_shown(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/customers/')
        self.assertNotContains(response, 'Cairo Gym')

    def test_search_by_name(self):
        Customer.objects.create(country=self.country, name='Gold Gym', segment='gym')

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/customers/', {'q': 'gold'})

        self.assertContains(response, 'Gold Gym')
        self.assertNotContains(response, 'Fitness First')

    def test_search_with_no_matches(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/customers/', {'q': 'nonexistent'})
        self.assertContains(response, 'No customers match that search.')


class CustomerCreateTests(CustomerTestCase):
    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get('/customers/new/')
        self.assertEqual(response.status_code, 403)

    def test_supervisor_gets_403(self):
        # Creating a customer used to be open to anyone with
        # manage_customers (supervisors have it by default) — narrowed to
        # admin-only, same reasoning as technician_create.
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/customers/new/')
        self.assertEqual(response.status_code, 403)

    def test_manager_gets_403(self):
        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Mona Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post('/customers/new/', {'name': 'Gold Gym', 'segment': 'gym'})
        self.assertEqual(response.status_code, 403)

    def test_admin_creates_a_customer(self):
        self.client.login(username='admin1', password='pass12345')
        response = self.client.post('/customers/new/', {'name': 'Gold Gym', 'segment': 'gym'})
        self.assertEqual(response.status_code, 302)

        customer = Customer.objects.get(name='Gold Gym')
        self.assertEqual(customer.country, self.country)
        self.assertEqual(customer.segment, 'gym')

    def test_hotel_segment_works(self):
        self.client.login(username='admin1', password='pass12345')
        response = self.client.post('/customers/new/', {'name': 'Grand Hotel', 'segment': 'hotel'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Customer.objects.filter(name='Grand Hotel', segment='hotel').exists())


class CustomerImportTests(CustomerTestCase):
    def _csv_file(self):
        content = (
            'customer_name,site_name,site_address\r\n'
            'Gold Gym,Main Branch,Somewhere\r\n'
        ).encode()
        return SimpleUploadedFile('customers.csv', content, content_type='text/csv')

    def test_supervisor_gets_403(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/customers/import/')
        self.assertEqual(response.status_code, 403)

    def test_manager_gets_403(self):
        manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Mona Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post('/customers/import/', {'csv_file': self._csv_file()})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Customer.objects.filter(name='Gold Gym').exists())

    def test_admin_imports_customers(self):
        self.client.login(username='admin1', password='pass12345')
        response = self.client.post('/customers/import/', {'csv_file': self._csv_file()})
        self.assertEqual(response.status_code, 200)

        customer = Customer.objects.get(name='Gold Gym')
        self.assertEqual(customer.country, self.country)
        self.assertTrue(Site.objects.filter(customer=customer, name='Main Branch').exists())


class CustomerDetailTests(CustomerTestCase):
    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(f'/customers/{self.customer.pk}/')
        self.assertEqual(response.status_code, 403)

    def test_other_country_customer_gives_404(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_customer = Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/customers/{other_customer.pk}/')
        self.assertEqual(response.status_code, 404)

    def test_shows_existing_sites(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/customers/{self.customer.pk}/')
        self.assertContains(response, 'Marina Branch')

    def test_supervisor_cannot_add_a_site(self):
        # Adding a site is admin-only now, same reasoning as
        # customer_create — a supervisor still sees the customer's
        # existing sites, just not the "Add a site" form.
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(f'/customers/{self.customer.pk}/', {
            'name': 'JBR Branch', 'address': 'JBR', 'contact_name': '', 'contact_phone': '',
            'contact_email': '', 'access_notes': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Site.objects.filter(customer=self.customer, name='JBR Branch').exists())
        self.assertIsNone(response.context['form'])

    def test_admin_adds_a_site(self):
        self.client.login(username='admin1', password='pass12345')
        response = self.client.post(f'/customers/{self.customer.pk}/', {
            'name': 'JBR Branch', 'address': 'JBR', 'contact_name': '', 'contact_phone': '',
            'contact_email': '', 'access_notes': '',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Site.objects.filter(customer=self.customer, name='JBR Branch').exists())

    def test_duplicate_site_name_is_rejected(self):
        self.client.login(username='admin1', password='pass12345')
        response = self.client.post(f'/customers/{self.customer.pk}/', {
            'name': 'Marina Branch', 'address': 'Dubai Marina', 'contact_name': '', 'contact_phone': '',
            'contact_email': '', 'access_notes': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors.get('name'))


class CustomerEditTests(CustomerTestCase):
    def setUp(self):
        super().setUp()
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Mona Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.url = f'/customers/{self.customer.pk}/edit/'

    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_supervisor_updates_customer_fields(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {
            'name': 'Fitness First Renamed', 'segment': 'gym', 'language': 'en',
            'contact_name': '', 'contact_phone': '', 'contact_email': '',
        })
        self.assertEqual(response.status_code, 302)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.name, 'Fitness First Renamed')

    def test_supervisor_can_set_code_and_shipping_address(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {
            'name': 'Fitness First', 'code': 'ACC-42', 'segment': 'gym', 'language': 'en',
            'contact_name': '', 'contact_phone': '', 'contact_email': '',
            'shipping_address': 'Head office, Warehouse 3',
        })
        self.assertEqual(response.status_code, 302)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.code, 'ACC-42')
        self.assertEqual(self.customer.shipping_address, 'Head office, Warehouse 3')

    def test_supervisor_cannot_create_a_login(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'create_login', 'email': 'contact@fitnessfirst.example'})
        self.assertEqual(response.status_code, 200)
        self.customer.refresh_from_db()
        self.assertIsNone(self.customer.user_id)

    def test_manager_can_create_a_login(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, {'action': 'create_login', 'email': 'contact@fitnessfirst.example'})
        self.assertEqual(response.status_code, 302)
        self.customer.refresh_from_db()
        self.assertIsNotNone(self.customer.user_id)
        self.assertEqual(self.customer.user.username, 'contact@fitnessfirst.example')
        self.assertEqual(self.customer.user.email, 'contact@fitnessfirst.example')
        self.assertTrue(self.customer.must_change_password)
        self.assertTrue(self.customer.user.has_usable_password())

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ['contact@fitnessfirst.example'])
        self.assertIn(self.customer.user.username, sent.body)

    def test_cannot_create_a_second_login_with_the_same_email(self):
        User.objects.create_user('taken@example.com', password='pass12345')
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, {'action': 'create_login', 'email': 'taken@example.com'})
        self.assertEqual(response.status_code, 200)
        self.customer.refresh_from_db()
        self.assertIsNone(self.customer.user_id)

    def test_manager_can_reset_the_customer_password(self):
        portal_user = User.objects.create_user('fitnessfirst', password='old-password')
        self.customer.user = portal_user
        self.customer.save(update_fields=['user'])

        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, {
            'action': 'reset_password', 'new_password1': 'br4nd-New-Pass!', 'new_password2': 'br4nd-New-Pass!',
        })
        self.assertEqual(response.status_code, 302)
        portal_user.refresh_from_db()
        self.assertTrue(portal_user.check_password('br4nd-New-Pass!'))

    def test_manager_can_reach_a_customer_in_another_country(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_customer = Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')

        self.client.login(username='manager1', password='pass12345')
        response = self.client.get(f'/customers/{other_customer.pk}/edit/')
        self.assertEqual(response.status_code, 200)

    def test_supervisor_cannot_reach_a_customer_in_another_country(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_customer = Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')

        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get(f'/customers/{other_customer.pk}/edit/')
        self.assertEqual(response.status_code, 404)


class CustomerDeactivateTests(CustomerTestCase):
    def setUp(self):
        super().setUp()
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Mona Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )
        self.portal_user = User.objects.create_user('fitnessfirst', password='pass12345')
        self.customer.user = self.portal_user
        self.customer.save(update_fields=['user'])
        self.url = f'/customers/{self.customer.pk}/edit/'

    def test_manager_deactivates_a_customer_and_blocks_their_login(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, {'action': 'deactivate', 'reason': 'Contract ended.'})
        self.assertEqual(response.status_code, 302)

        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_active)
        self.assertEqual(self.customer.deactivation_reason, 'Contract ended.')
        self.portal_user.refresh_from_db()
        self.assertFalse(self.portal_user.is_active)

    def test_supervisor_cannot_deactivate(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(self.url, {'action': 'deactivate', 'reason': 'Contract ended.'})
        self.assertEqual(response.status_code, 200)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.is_active)

    def test_manager_reactivates_a_customer(self):
        self.customer.set_active(False, reason='Contract ended.')

        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, {'action': 'reactivate'})
        self.assertEqual(response.status_code, 302)

        self.customer.refresh_from_db()
        self.assertTrue(self.customer.is_active)
        self.assertEqual(self.customer.deactivation_reason, '')
        self.portal_user.refresh_from_db()
        self.assertTrue(self.portal_user.is_active)


class AllCustomersTests(CustomerTestCase):
    def setUp(self):
        super().setUp()
        self.manager_user = User.objects.create_user('manager1', password='pass12345')
        Technician.objects.create(
            user=self.manager_user, country=self.country, full_name='Mona Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )

    def test_supervisor_gets_403(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.get('/customers/all/')
        self.assertEqual(response.status_code, 403)

    def test_manager_sees_customers_from_every_country(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')

        self.client.login(username='manager1', password='pass12345')
        response = self.client.get('/customers/all/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fitness First')
        self.assertContains(response, 'Cairo Gym')


class SiteEditTests(CustomerTestCase):
    def test_technician_gets_403(self):
        self.client.login(username='tech1', password='pass12345')
        response = self.client.get(f'/customers/sites/{self.site.pk}/edit/')
        self.assertEqual(response.status_code, 403)

    def test_supervisor_updates_a_site(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(f'/customers/sites/{self.site.pk}/edit/', {
            'name': 'Marina Branch', 'address': 'Updated address', 'contact_name': '',
            'contact_phone': '', 'contact_email': '', 'access_notes': '',
        })
        self.assertEqual(response.status_code, 302)
        self.site.refresh_from_db()
        self.assertEqual(self.site.address, 'Updated address')


class PortalLoginTests(CustomerTestCase):
    def setUp(self):
        super().setUp()
        self.portal_user = User.objects.create_user('fitnessfirst', password='pass12345')
        self.customer.user = self.portal_user
        self.customer.save(update_fields=['user'])

    def test_customer_can_log_in_and_reaches_portal_home(self):
        response = self.client.post('/customers/portal/login/', {
            'username': 'fitnessfirst', 'password': 'pass12345',
        })
        self.assertRedirects(response, '/customers/portal/')

    def test_staff_login_is_rejected_on_the_portal_page(self):
        response = self.client.post('/customers/portal/login/', {
            'username': 'supervisor1', 'password': 'pass12345',
        }, follow=True)
        self.assertContains(response, 'a customer account')

    def test_open_redirect_is_blocked(self):
        response = self.client.post(
            '/customers/portal/login/?next=https://evil.example/steal',
            {'username': 'fitnessfirst', 'password': 'pass12345'},
        )
        self.assertRedirects(response, '/customers/portal/')

    def test_temporary_password_redirects_to_first_login(self):
        self.customer.must_change_password = True
        self.customer.save(update_fields=['must_change_password'])

        response = self.client.post('/customers/portal/login/', {
            'username': 'fitnessfirst', 'password': 'pass12345',
        })
        self.assertRedirects(response, '/customers/portal/first-login/')


@override_settings(AXES_ENABLED=True)
class PortalLoginLockoutTests(CustomerTestCase):
    """django-axes hooks into django.contrib.auth.authenticate() at the
    backend level (see AUTHENTICATION_BACKENDS, spots/settings.py) — the
    same call AuthenticationForm makes from portal_login as from the
    staff login view, so the brute-force lockout that protects
    /accounts/login/ (people.tests.LoginLockoutTests) should equally
    protect a customer's portal login. Confirmed here rather than
    assumed, since it's a separate view and easy to accidentally bypass.
    """

    def setUp(self):
        super().setUp()
        self.portal_user = User.objects.create_user('fitnessfirst', password='pass12345')
        self.customer.user = self.portal_user
        self.customer.save(update_fields=['user'])

    def test_locks_out_after_repeated_failures(self):
        for _ in range(5):
            self.client.post('/customers/portal/login/', {'username': 'fitnessfirst', 'password': 'wrong'})

        response = self.client.post('/customers/portal/login/', {
            'username': 'fitnessfirst', 'password': 'pass12345',
        })
        self.assertEqual(response.status_code, 429)


class PortalFirstLoginTests(CustomerTestCase):
    def setUp(self):
        super().setUp()
        self.portal_user = User.objects.create_user('fitnessfirst', password='pass12345')
        self.customer.user = self.portal_user
        self.customer.must_change_password = True
        self.customer.save(update_fields=['user', 'must_change_password'])
        self.client.login(username='fitnessfirst', password='pass12345')

    def test_shown_while_must_change_password_is_set(self):
        response = self.client.get('/customers/portal/first-login/')
        self.assertEqual(response.status_code, 200)

    def test_redirects_to_portal_home_once_cleared(self):
        self.customer.must_change_password = False
        self.customer.save(update_fields=['must_change_password'])

        response = self.client.get('/customers/portal/first-login/')
        self.assertRedirects(response, '/customers/portal/')

    def test_submitting_sets_password_and_details_and_clears_the_flag(self):
        response = self.client.post('/customers/portal/first-login/', {
            'contact_name': 'Sara Ali', 'contact_phone': '0509876543', 'contact_email': 'sara@fitnessfirst.ae',
            'new_password1': 'br4nd-New-Pass!', 'new_password2': 'br4nd-New-Pass!',
        })
        self.assertRedirects(response, '/customers/portal/')

        self.customer.refresh_from_db()
        self.assertFalse(self.customer.must_change_password)
        self.assertEqual(self.customer.contact_name, 'Sara Ali')
        self.assertEqual(self.customer.contact_phone, '0509876543')
        self.assertEqual(self.customer.contact_email, 'sara@fitnessfirst.ae')

        self.portal_user.refresh_from_db()
        self.assertTrue(self.portal_user.check_password('br4nd-New-Pass!'))

    def test_portal_home_is_blocked_until_the_form_is_submitted(self):
        response = self.client.get('/customers/portal/')
        self.assertRedirects(response, '/customers/portal/first-login/')


class PortalHomeTests(CustomerTestCase):
    def setUp(self):
        super().setUp()
        self.portal_user = User.objects.create_user('fitnessfirst', password='pass12345')
        self.customer.user = self.portal_user
        self.customer.save(update_fields=['user'])

    def test_anonymous_is_redirected_to_portal_login(self):
        response = self.client.get('/customers/portal/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/customers/portal/login/', response.url)

    def test_shows_only_this_customers_own_tickets(self):
        CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0001', customer=self.customer, company_name='Fitness First',
            site_description='Marina Branch', site_address='Dubai Marina',
            contact_name='Ali', contact_phone='0501234567', description='Belt noise',
            submitted_at=timezone.now(),
        )
        other_customer = Customer.objects.create(country=self.country, name='Other Gym', segment='gym')
        CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0002', customer=other_customer, company_name='Other Gym',
            site_description='Faraway Branch', site_address='Somewhere',
            contact_name='Bob', contact_phone='0507654321', description='Other issue',
            submitted_at=timezone.now(),
        )

        self.client.login(username='fitnessfirst', password='pass12345')
        response = self.client.get('/customers/portal/')
        self.assertContains(response, 'Marina Branch')
        self.assertNotContains(response, 'Faraway Branch')

    def test_shows_tickets_from_every_one_of_the_customers_branches(self):
        jbr_site = Site.objects.create(customer=self.customer, name='JBR Branch', address='JBR, Dubai')
        CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0001', customer=self.customer, site=self.site, company_name='Fitness First',
            site_description='Marina Branch', site_address='Dubai Marina',
            contact_name='Ali', contact_phone='0501234567', description='Belt noise',
            submitted_at=timezone.now(),
        )
        CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0002', customer=self.customer, site=jbr_site, company_name='Fitness First',
            site_description='JBR Branch', site_address='JBR, Dubai',
            contact_name='Sara', contact_phone='0509876543', description='Bike display broken',
            submitted_at=timezone.now(),
        )

        self.client.login(username='fitnessfirst', password='pass12345')
        response = self.client.get('/customers/portal/')
        self.assertContains(response, 'Marina Branch')
        self.assertContains(response, 'JBR Branch')


class PortalTicketNewTests(CustomerTestCase):
    def setUp(self):
        super().setUp()
        self.portal_user = User.objects.create_user('fitnessfirst', password='pass12345')
        self.customer.user = self.portal_user
        self.customer.save(update_fields=['user'])
        self.other_customer = Customer.objects.create(country=self.country, name='Other Gym', segment='gym')
        self.other_site = Site.objects.create(customer=self.other_customer, name='Other Branch', address='Elsewhere')

    def _payload(self, **overrides):
        payload = {
            'site': self.site.pk, 'contact_name': 'Ali', 'contact_phone': '0501234567',
            'contact_email': '', 'serial_numbers': 'SN-1', 'description': 'Belt noise', 'notes': '',
        }
        payload.update(overrides)
        return payload

    def test_customer_can_submit_a_ticket_for_their_own_site(self):
        self.client.login(username='fitnessfirst', password='pass12345')
        response = self.client.post('/customers/portal/tickets/new/', self._payload())
        self.assertEqual(response.status_code, 302)

        ticket = CustomerTicket.objects.get()
        self.assertEqual(ticket.customer, self.customer)
        self.assertEqual(ticket.site, self.site)
        self.assertEqual(ticket.site_description, 'Marina Branch')
        self.assertTrue(ticket.ticket_number)

    def test_submitting_creates_a_new_ticket_notification(self):
        self.client.login(username='fitnessfirst', password='pass12345')
        self.client.post('/customers/portal/tickets/new/', self._payload())

        ticket = CustomerTicket.objects.get()
        notification = ticket.notifications.get()
        self.assertEqual(notification.kind, TicketNotification.Kind.NEW_TICKET)
        self.assertIsNone(notification.seen_at)

    def test_submitting_with_a_contact_email_sends_a_confirmation(self):
        self.client.login(username='fitnessfirst', password='pass12345')
        self.client.post('/customers/portal/tickets/new/', self._payload(contact_email='ali@fitnessfirst.example'))

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('received', mail.outbox[0].subject)
        self.assertIn('ali@fitnessfirst.example', mail.outbox[0].to)

    def test_submitting_notifies_the_technical_support_manager_by_email(self):
        # manage_tickets lives on the technical support manager role now,
        # not plain Manager — the new-ticket email follows that permission.
        support_user = User.objects.create_user('support1', email='sara@example.com', password='pass12345')
        Technician.objects.create(
            user=support_user, country=self.country, full_name='Sara Support',
            language='en', role=Technician.Role.SUPPORT_MANAGER, employment_type='staff',
        )

        self.client.login(username='fitnessfirst', password='pass12345')
        self.client.post('/customers/portal/tickets/new/', self._payload())

        ticket = CustomerTicket.objects.get()
        staff_emails = [email for email in mail.outbox if email.to == [support_user.email]]
        self.assertEqual(len(staff_emails), 1)
        self.assertIn(ticket.ticket_number, staff_emails[0].subject)

    def test_submitting_does_not_notify_a_plain_manager_by_email(self):
        manager_user = User.objects.create_user('manager1', email='dana@example.com', password='pass12345')
        Technician.objects.create(
            user=manager_user, country=self.country, full_name='Dana Manager',
            language='en', role=Technician.Role.MANAGER, employment_type='staff',
        )

        self.client.login(username='fitnessfirst', password='pass12345')
        self.client.post('/customers/portal/tickets/new/', self._payload())

        staff_emails = [email for email in mail.outbox if email.to == [manager_user.email]]
        self.assertEqual(len(staff_emails), 0)

    def test_submitting_without_a_contact_email_sends_nothing(self):
        self.client.login(username='fitnessfirst', password='pass12345')
        self.client.post('/customers/portal/tickets/new/', self._payload())

        self.assertEqual(len(mail.outbox), 0)

    def test_customer_cannot_submit_a_ticket_for_another_customers_site(self):
        self.client.login(username='fitnessfirst', password='pass12345')
        response = self.client.post('/customers/portal/tickets/new/', self._payload(site=self.other_site.pk))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CustomerTicket.objects.exists())

    def test_customer_on_a_temporary_password_is_redirected_to_first_login(self):
        self.customer.must_change_password = True
        self.customer.save(update_fields=['must_change_password'])

        self.client.login(username='fitnessfirst', password='pass12345')
        response = self.client.get('/customers/portal/tickets/new/')
        self.assertRedirects(response, '/customers/portal/first-login/')

        response = self.client.post('/customers/portal/tickets/new/', self._payload())
        self.assertRedirects(response, '/customers/portal/first-login/')
        self.assertFalse(CustomerTicket.objects.exists())

    def test_customer_code_is_copied_from_the_customer_account(self):
        self.customer.code = 'ACC-42'
        self.customer.save(update_fields=['code'])

        self.client.login(username='fitnessfirst', password='pass12345')
        self.client.post('/customers/portal/tickets/new/', self._payload())

        ticket = CustomerTicket.objects.get()
        self.assertEqual(ticket.customer_code, 'ACC-42')

    def test_shipping_address_defaults_to_the_customers_but_can_be_overridden(self):
        self.customer.shipping_address = 'Head office, Warehouse 3'
        self.customer.save(update_fields=['shipping_address'])

        self.client.login(username='fitnessfirst', password='pass12345')
        response = self.client.get('/customers/portal/tickets/new/')
        self.assertContains(response, 'Head office, Warehouse 3')

        self.client.post('/customers/portal/tickets/new/', self._payload(shipping_address='Ship to the gym instead'))
        ticket = CustomerTicket.objects.get()
        self.assertEqual(ticket.shipping_address, 'Ship to the gym instead')

    def test_attachment_is_stored_under_an_unguessable_path(self):
        # Whatever serves MEDIA_URL applies no per-file login check, so a
        # predictable path (the original filename alone) would let anyone
        # who guesses it — a common phone-camera name like IMG_0001.jpg —
        # view another customer's fault photo with no login at all.
        photo = SimpleUploadedFile('IMG_0001.jpg', b'fake-image-bytes', content_type='image/jpeg')
        self.client.login(username='fitnessfirst', password='pass12345')
        self.client.post('/customers/portal/tickets/new/', self._payload(attachments=[photo]))

        attachment = CustomerTicket.objects.get().attachments.get()
        self.assertNotEqual(attachment.file.name, 'ticket_attachments/IMG_0001.jpg')
        self.assertTrue(attachment.file.name.endswith('/IMG_0001.jpg'))


class SiteMapLocationTests(CustomerTestCase):
    def _post(self, map_location):
        return self.client.post(f'/customers/sites/{self.site.pk}/edit/', {
            'name': 'Marina Branch', 'address': 'Dubai Marina', 'contact_name': '', 'contact_phone': '',
            'contact_email': '', 'access_notes': '', 'map_location': map_location,
        })

    def test_coordinates_or_a_maps_link_set_the_location(self):
        self.client.login(username='supervisor1', password='pass12345')
        for text in (
            '25.0772, 55.1306',
            'https://www.google.com/maps/place/Gym/@25.0772,55.1306,17z/data=x',
            'https://www.google.com/maps/place/Gym/data=!3d25.0772!4d55.1306',
            'https://maps.google.com/?q=25.0772,55.1306',
        ):
            with self.subTest(text=text):
                self.assertEqual(self._post(text).status_code, 302)
                self.site.refresh_from_db()
                self.assertEqual(float(self.site.latitude), 25.0772)
                self.assertEqual(float(self.site.longitude), 55.1306)
                self.assertEqual(self.site.location_source, 'office')

    def test_a_link_without_coordinates_is_refused(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self._post('https://maps.app.goo.gl/abc123')
        self.assertEqual(response.status_code, 200)
        self.assertIn('map_location', response.context['form'].errors)

    def test_saving_unchanged_keeps_where_it_came_from(self):
        self.site.latitude, self.site.longitude, self.site.location_source = 25.0772, 55.1306, 'arrival'
        self.site.save()
        self.client.login(username='supervisor1', password='pass12345')
        initial = self.client.get(f'/customers/sites/{self.site.pk}/edit/').context['form']['map_location'].initial
        self._post(initial)
        self.site.refresh_from_db()
        self.assertEqual(self.site.location_source, 'arrival')
