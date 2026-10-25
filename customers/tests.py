from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from people.models import Technician
from reference.models import Country
from tasks.models import CustomerTicket, TicketNotification

from .models import Asset, Customer, Site

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
        self.assertContains(response, 'No customers match your search.')


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

    def test_name_and_email_alone_are_enough(self):
        self.customer.contact_email = ''
        self.customer.save(update_fields=['contact_email'])
        content = (
            'customer_name,customer_contact_email\r\n'
            'Gold Gym,info@goldgym.example\r\n'
            'Fitness First,contact@fitnessfirst.example\r\n'
            ',nobody@example.com\r\n'
        ).encode()
        self.client.login(username='admin1', password='pass12345')
        response = self.client.post('/customers/import/', {
            'csv_file': SimpleUploadedFile('customers.csv', content, content_type='text/csv'),
        })

        outcomes = [result['outcome'] for result in response.context['results']]
        self.assertEqual(outcomes, ['created', 'created', 'error'])
        gold = Customer.objects.get(name='Gold Gym')
        self.assertEqual(gold.contact_email, 'info@goldgym.example')
        self.assertFalse(gold.sites.exists())
        # An existing customer without an email picks one up; nothing is duplicated.
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.contact_email, 'contact@fitnessfirst.example')
        self.assertEqual(Customer.objects.filter(name='Fitness First').count(), 1)

    def test_a_site_still_needs_its_address(self):
        content = 'customer_name,site_name\r\nGold Gym,Main Branch\r\n'.encode()
        self.client.login(username='admin1', password='pass12345')
        response = self.client.post('/customers/import/', {
            'csv_file': SimpleUploadedFile('customers.csv', content, content_type='text/csv'),
        })
        self.assertEqual(response.context['results'][0]['outcome'], 'error')
        self.assertFalse(Customer.objects.filter(name='Gold Gym').exists())


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
        self.assertIsNotNone(self.customer.invited_at)
        # No password exists until the customer sets one through the link.
        self.assertFalse(self.customer.user.has_usable_password())

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ['contact@fitnessfirst.example'])
        self.assertIn(self.customer.user.username, sent.body)
        self.assertIn('/customers/portal/account/', sent.body)

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
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.must_change_password)

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
            'billing_address': 'PO Box 1, Dubai', 'shipping_address': 'Warehouse 3, Al Quoz',
            'new_password1': 'br4nd-New-Pass!', 'new_password2': 'br4nd-New-Pass!',
        })
        self.assertRedirects(response, '/customers/portal/')

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.billing_address, 'PO Box 1, Dubai')
        self.assertEqual(self.customer.shipping_address, 'Warehouse 3, Al Quoz')
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



class PortalVisitReportTests(CustomerTestCase):
    # Customers download their own visit reports from the portal's service history.

    def setUp(self):
        super().setUp()
        from reports.models import WorkReport
        from tasks.models import Task
        self.portal_user = User.objects.create_user('fitnessfirst', password='pass12345')
        self.customer.user = self.portal_user
        self.customer.save(update_fields=['user'])
        self.task = Task.objects.create(
            task_number='AE-0801', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CONTRACT, reported_at=timezone.now(), created_by=self.supervisor_user,
            status=Task.Status.CLOSED, scheduled_for=timezone.now(),
        )
        WorkReport.objects.create(
            task=self.task, findings='Belt worn out', action_taken='Replaced belt', resolved=True,
            labour_hours='1.50', customer_name='Ali', submitted_at=timezone.now(),
        )
        self.url = f'/customers/portal/visits/{self.task.pk}/report.pdf'

    def test_the_customer_downloads_their_own_report(self):
        self.client.login(username='fitnessfirst', password='pass12345')
        self.assertContains(self.client.get('/customers/portal/'), self.url)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_another_customers_report_is_not_found(self):
        other = Customer.objects.create(country=self.country, name='Other Gym', segment='gym')
        other.user = User.objects.create_user('othergym', password='pass12345')
        other.save(update_fields=['user'])
        self.client.login(username='othergym', password='pass12345')
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_a_visit_without_a_report_is_not_found(self):
        self.task.report.delete()
        self.client.login(username='fitnessfirst', password='pass12345')
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_staff_cannot_use_the_portal_link(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.assertIn(self.client.get(self.url).status_code, (302, 403, 404))


class PortalTicketNewTests(CustomerTestCase):
    def setUp(self):
        super().setUp()
        self.portal_user = User.objects.create_user('fitnessfirst', password='pass12345')
        self.customer.user = self.portal_user
        self.customer.save(update_fields=['user'])
        self.other_customer = Customer.objects.create(country=self.country, name='Other Gym', segment='gym')
        self.other_site = Site.objects.create(customer=self.other_customer, name='Other Branch', address='Elsewhere')

    def _payload(self, machines=None, **overrides):
        payload = {
            'site': self.site.pk, 'contact_name': 'Ali', 'contact_phone': '0501234567',
            'contact_email': '', 'notes': '',
            'machines-TOTAL_FORMS': '10', 'machines-INITIAL_FORMS': '0',
            'machines-MIN_NUM_FORMS': '0', 'machines-MAX_NUM_FORMS': '10',
        }
        if machines is None:
            machines = [{'model_name': 'Treadmill', 'serial_no': 'SN-1', 'problem': 'noise', 'description': 'Belt noise'}]
        for index, machine in enumerate(machines):
            # A described problem is required; tests about other things get one.
            if machine and 'description' not in machine:
                machine = {**machine, 'description': 'Stops after 5 minutes, error E-07, since Monday.'}
            payload.update({f'machines-{index}-{key}': value for key, value in machine.items()})
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
        self.client.post('/customers/portal/tickets/new/', self._payload(**{'machines-0-files': [photo]}))

        attachment = CustomerTicket.objects.get().attachments.get()
        self.assertNotEqual(attachment.file.name, 'ticket_attachments/IMG_0001.jpg')
        self.assertTrue(attachment.file.name.endswith('/IMG_0001.jpg'))


class PortalTicketMachinesTests(PortalTicketNewTests):
    # Each machine is its own card with its own photos (TicketMachine).

    def setUp(self):
        super().setUp()
        from reference.models import Brand
        self.brand = Brand.objects.create(name='Technogym')
        self.asset = Asset.objects.create(site=self.site, brand=self.brand, model_name='Excite Run 700', serial_no='TG-77')

    def test_a_registered_machine_can_be_picked(self):
        self.client.login(username='fitnessfirst', password='pass12345')
        self.client.post('/customers/portal/tickets/new/', self._payload(machines=[
            {'asset': str(self.asset.pk), 'problem': 'not_working', 'out_of_service': 'on'},
            {'model_name': 'Old bike', 'serial_no': 'B-9', 'problem': 'display'},
        ]))
        ticket = CustomerTicket.objects.get()
        first, second = ticket.machines.all()
        self.assertEqual((first.asset, first.machine_name, first.serial, first.out_of_service),
                         (self.asset, 'Technogym Excite Run 700', 'TG-77', True))
        self.assertEqual((second.asset, second.machine_name, second.serial), (None, 'Old bike', 'B-9'))
        # The ticket's text fields are written from the machines for everything that reads them.
        self.assertEqual(ticket.serial_numbers, 'TG-77' + chr(10) + 'B-9')
        self.assertIn('Technogym Excite Run 700 (TG-77): Not working', ticket.description)
        self.assertIn('cannot be used', ticket.description)

    def test_a_machine_from_another_site_is_rejected(self):
        other_site = Site.objects.create(customer=self.customer, name='JBR Branch', address='JBR')
        self.client.login(username='fitnessfirst', password='pass12345')
        response = self.client.post('/customers/portal/tickets/new/', self._payload(
            site=other_site.pk, machines=[{'asset': str(self.asset.pk), 'problem': 'noise'}],
        ))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CustomerTicket.objects.exists())

    def test_at_least_one_machine_is_needed(self):
        self.client.login(username='fitnessfirst', password='pass12345')
        response = self.client.post('/customers/portal/tickets/new/', self._payload(machines=[]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CustomerTicket.objects.exists())

    def test_a_machine_needs_its_problem_described(self):
        self.client.login(username='fitnessfirst', password='pass12345')
        response = self.client.post('/customers/portal/tickets/new/', self._payload(machines=[
            {'model_name': 'Treadmill', 'serial_no': 'SN-1', 'problem': 'noise', 'description': '  '},
        ]))
        self.assertContains(response, 'Please describe the problem')
        self.assertFalse(CustomerTicket.objects.exists())

    def test_a_machine_needs_a_problem_and_an_identity(self):
        self.client.login(username='fitnessfirst', password='pass12345')
        response = self.client.post('/customers/portal/tickets/new/', self._payload(machines=[{'description': 'Squeaks'}]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CustomerTicket.objects.exists())

    def test_an_unlisted_machine_needs_its_typed_serial_a_photo_is_extra(self):
        # The typed number is how the machine is tracked; a sticker photo
        # alone isn't enough, but it's kept alongside to check it.
        self.client.login(username='fitnessfirst', password='pass12345')
        sticker = SimpleUploadedFile('sticker.jpg', b'fake-image-bytes', content_type='image/jpeg')
        response = self.client.post('/customers/portal/tickets/new/', self._payload(machines=[
            {'model_name': 'Old bike', 'problem': 'display', 'serial_photo': sticker},
        ]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CustomerTicket.objects.exists())

        sticker = SimpleUploadedFile('sticker.jpg', b'fake-image-bytes', content_type='image/jpeg')
        self.client.post('/customers/portal/tickets/new/', self._payload(machines=[
            {'model_name': 'Old bike', 'serial_no': 'OB-9', 'problem': 'display', 'serial_photo': sticker},
        ]))
        machine = CustomerTicket.objects.get().machines.get()
        self.assertEqual(machine.serial_no, 'OB-9')
        self.assertTrue(machine.serial_photo.name.endswith('/sticker.jpg'))

    def test_a_listed_machine_needs_no_serial(self):
        self.client.login(username='fitnessfirst', password='pass12345')
        self.client.post('/customers/portal/tickets/new/', self._payload(machines=[
            {'asset': str(self.asset.pk), 'problem': 'noise'},
        ]))
        self.assertEqual(CustomerTicket.objects.get().machines.get().serial, 'TG-77')

    def test_photos_belong_to_their_machine(self):
        photo = SimpleUploadedFile('belt.jpg', b'fake-image-bytes', content_type='image/jpeg')
        self.client.login(username='fitnessfirst', password='pass12345')
        self.client.post('/customers/portal/tickets/new/', self._payload(machines=[
            {'model_name': 'Treadmill', 'serial_no': 'T-1', 'problem': 'noise'},
            {'model_name': 'Bike', 'serial_no': 'B-1', 'problem': 'display', 'files': [photo]},
        ]))
        ticket = CustomerTicket.objects.get()
        self.assertEqual(ticket.machines.get(model_name='Bike').attachments.count(), 1)
        self.assertEqual(ticket.machines.get(model_name='Treadmill').attachments.count(), 0)

    def test_the_office_and_the_customer_see_the_machines_table(self):
        self.client.login(username='fitnessfirst', password='pass12345')
        self.client.post('/customers/portal/tickets/new/', self._payload(machines=[
            {'asset': str(self.asset.pk), 'problem': 'not_working'},
        ]))
        ticket = CustomerTicket.objects.get()
        status_page = self.client.get(f'/tasks/tickets/status/{ticket.token}/')
        self.assertContains(status_page, 'Technogym Excite Run 700')


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


class CustomerAccountsTests(CustomerTestCase):
    """The Customer accounts screen: Admin, the Technical Support Manager
    and the Operations Manager invite customers, see every account's
    status, and fix whatever's wrong."""

    def setUp(self):
        super().setUp()
        self.customer.contact_email = 'contact@fitnessfirst.example'
        self.customer.save(update_fields=['contact_email'])
        self.url = '/customers/accounts/'

    def _staff(self, username, role):
        Technician.objects.create(
            user=User.objects.create_user(username, password='pass12345'), country=self.country,
            full_name=username.title(), language='en', role=role, employment_type='staff',
        )
        self.client.login(username=username, password='pass12345')

    def _statuses(self):
        return {row['customer'].name: row['status'] for row in self.client.get(self.url).context['page_obj']}

    def test_admin_tech_support_and_operations_managers_get_in(self):
        self.client.login(username='admin1', password='pass12345')
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self._staff('support1', Technician.Role.SUPPORT_MANAGER)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self._staff('ops1', Technician.Role.OPERATIONS_MANAGER)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_other_roles_get_403(self):
        for username, role in [('manager1', Technician.Role.MANAGER), ('csm1', Technician.Role.COUNTRY_SUPPORT)]:
            with self.subTest(role=role):
                self._staff(username, role)
                self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.login(username='supervisor1', password='pass12345')
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_statuses(self):
        Customer.objects.create(country=self.country, name='No Email Gym', segment='gym')
        self.client.login(username='admin1', password='pass12345')
        statuses = self._statuses()
        self.assertEqual(statuses['Fitness First'], 'not_invited')
        self.assertEqual(statuses['No Email Gym'], 'no_email')

    def test_bulk_invite_sends_one_bilingual_email_each_and_logs_it(self):
        from django.contrib.admin.models import LogEntry

        other = Customer.objects.create(
            country=self.country, name='Gold Gym', segment='gym', contact_email='gold@example.com',
        )
        self.client.login(username='admin1', password='pass12345')
        self.client.post(self.url, {'action': 'invite_selected', 'customers': [self.customer.pk, other.pk]})

        self.assertEqual(len(mail.outbox), 2)
        sent = mail.outbox[0]
        self.assertIn('Welcome to the Profit Sports Solutions customer portal', sent.subject)
        self.assertIn('Dear', sent.body)
        self.assertIn('/customers/portal/account/', sent.body)
        html = sent.alternatives[0][0]
        self.assertIn('dir="rtl"', html)
        self.assertEqual(self._statuses()['Fitness First'], 'invited')
        self.assertEqual(LogEntry.objects.filter(change_message='Portal welcome email sent').count(), 2)

    def test_welcome_link_completes_the_account_and_works_once(self):
        self.client.login(username='admin1', password='pass12345')
        self.client.post(self.url, {'action': 'invite', 'customer': self.customer.pk})
        link = next(line for line in mail.outbox[0].body.splitlines() if '/customers/portal/account/' in line)
        path = link[link.index('/customers/'):]
        self.client.logout()

        form = self.client.get(path).context['form']
        self.assertIn('billing_address', form.fields)
        response = self.client.post(path, {
            'contact_name': 'Sara Ali', 'contact_phone': '0509876543', 'contact_email': 'sara@fitnessfirst.ae',
            'billing_address': 'PO Box 1, Dubai', 'shipping_address': 'Warehouse 3, Al Quoz',
            'new_password1': 'br4nd-New-Pass!', 'new_password2': 'br4nd-New-Pass!',
        })
        self.assertRedirects(response, '/customers/portal/')
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.must_change_password)
        self.assertEqual(self.customer.billing_address, 'PO Box 1, Dubai')
        self.assertTrue(self.customer.user.check_password('br4nd-New-Pass!'))

        self.client.logout()
        self.assertEqual(self.client.get(path).status_code, 400)

    def test_an_expired_invitation_shows_and_can_be_resent(self):
        self.client.login(username='admin1', password='pass12345')
        self.client.post(self.url, {'action': 'invite', 'customer': self.customer.pk})
        Customer.objects.filter(pk=self.customer.pk).update(invited_at=timezone.now() - timezone.timedelta(days=8))
        self.assertEqual(self._statuses()['Fitness First'], 'expired')
        self.client.post(self.url, {'action': 'invite', 'customer': self.customer.pk})
        self.assertEqual(self._statuses()['Fitness First'], 'invited')
        self.assertEqual(len(mail.outbox), 2)

    def test_disable_enable_and_temporary_password(self):
        user = User.objects.create_user('contact@fitnessfirst.example', password='pass12345')
        self.customer.user = user
        self.customer.save(update_fields=['user'])
        self.client.login(username='admin1', password='pass12345')

        self.client.post(self.url, {'action': 'disable', 'customer': self.customer.pk})
        self.assertEqual(self._statuses()['Fitness First'], 'disabled')
        self.client.post(self.url, {'action': 'enable', 'customer': self.customer.pk})
        self.assertEqual(self._statuses()['Fitness First'], 'active')

        response = self.client.post(self.url, {'action': 'temporary_password', 'customer': self.customer.pk})
        password = response.context['password']
        user.refresh_from_db()
        self.assertTrue(user.check_password(password))
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.must_change_password)
        self.assertEqual(len(mail.outbox), 0)

    def test_change_login_email(self):
        user = User.objects.create_user('old@example.com', email='old@example.com', password='pass12345')
        self.customer.user = user
        self.customer.save(update_fields=['user'])
        self.client.login(username='admin1', password='pass12345')
        self.client.post(self.url, {'action': 'change_email', 'customer': self.customer.pk, 'email': 'new@example.com'})
        user.refresh_from_db()
        self.assertEqual((user.username, user.email), ('new@example.com', 'new@example.com'))


class QuickInviteTests(CustomerTestCase):
    """A new customer from just a name and an email, invited at once."""

    def setUp(self):
        super().setUp()
        self.client.login(username='admin1', password='pass12345')
        self.url = '/customers/accounts/'

    def _invite(self, **overrides):
        return self.client.post(self.url, {
            'action': 'quick_invite', 'name': 'Gold Gym', 'email': 'info@goldgym.example',
            'country': self.country.pk, 'segment': 'gym', **overrides,
        })

    def test_creates_the_customer_and_sends_the_welcome_email(self):
        from django.contrib.admin.models import LogEntry

        self._invite()
        customer = Customer.objects.get(name='Gold Gym')
        self.assertEqual(customer.country, self.country)
        self.assertEqual(customer.user.username, 'info@goldgym.example')
        self.assertTrue(customer.must_change_password)
        self.assertEqual(mail.outbox[0].to, ['info@goldgym.example'])
        self.assertTrue(LogEntry.objects.filter(change_message='Customer created and portal welcome email sent').exists())

    def test_country_and_segment_must_be_chosen(self):
        form = self.client.get(self.url).context['quick_invite_form']
        self.assertIsNone(form['country'].value())
        self._invite(country='', segment='')
        self.assertFalse(Customer.objects.filter(name='Gold Gym').exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_rejects_a_taken_email_or_an_existing_name(self):
        User.objects.create_user('info@goldgym.example', password='pass12345')
        self._invite()
        self._invite(name='fitness first', email='new@example.com')
        self.assertFalse(Customer.objects.filter(name='Gold Gym').exists())
        self.assertEqual(Customer.objects.filter(name__iexact='fitness first').count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_arabic_only_for_customers_in_arab_countries(self):
        self._invite()
        self.assertIn('dir="rtl"', mail.outbox[0].alternatives[0][0])
        self.assertIn('|', mail.outbox[0].subject)

        canada = Country.objects.create(name='Canada', iso_code='CA', timezone='America/Toronto', currency_code='CAD')
        self._invite(name='Maple Fitness', email='hello@maple.example', country=canada.pk)
        self.assertNotIn('dir="rtl"', mail.outbox[1].alternatives[0][0])
        self.assertNotIn('|', mail.outbox[1].subject)

    def test_other_roles_cannot_invite(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.assertEqual(self._invite().status_code, 403)
        self.assertFalse(Customer.objects.filter(name='Gold Gym').exists())


class PortalPasswordHelpTests(CustomerTestCase):
    def test_sends_a_link_to_a_customer_and_says_the_same_to_anyone(self):
        self.customer.user = User.objects.create_user(
            'contact@fitnessfirst.example', email='contact@fitnessfirst.example', password='pass12345',
        )
        self.customer.save(update_fields=['user'])

        known = self.client.post('/customers/portal/password-help/', {'email': 'contact@fitnessfirst.example'})
        unknown = self.client.post('/customers/portal/password-help/', {'email': 'nobody@example.com'})
        self.assertTrue(known.context['sent'])
        self.assertTrue(unknown.context['sent'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Set a new password', mail.outbox[0].subject)

    def test_staff_logins_get_nothing(self):
        self.client.post('/customers/portal/password-help/', {'email': 'admin1'})
        self.assertEqual(len(mail.outbox), 0)


@override_settings(AXES_ENABLED=True)
class CustomerAccountUnlockTests(CustomerTestCase):
    def setUp(self):
        super().setUp()
        self.customer.user = User.objects.create_user('fitnessfirst', password='pass12345')
        self.customer.save(update_fields=['user'])

    def test_locked_account_shows_in_the_bell_and_unlocks_at_once(self):
        for _ in range(5):
            self.client.post('/customers/portal/login/', {'username': 'fitnessfirst', 'password': 'wrong'})
        self.client.logout()

        from customers.accounts import locked_usernames
        self.assertIn('fitnessfirst', locked_usernames())

        staff = self.client_class()
        staff.force_login(self.admin_user)
        response = staff.get('/customers/accounts/')
        self.assertEqual(
            {row['customer'].name: row['status'] for row in response.context['page_obj']}['Fitness First'], 'locked',
        )
        kinds = [item['kind'] for item in response.context['unseen_notifications']]
        self.assertIn('customer_locked', kinds)

        staff.post('/customers/accounts/', {'action': 'unlock', 'customer': self.customer.pk})
        self.assertNotIn('fitnessfirst', locked_usernames())
        response = self.client.post('/customers/portal/login/', {'username': 'fitnessfirst', 'password': 'pass12345'})
        self.assertEqual(response.status_code, 302)


class PortalMachineQuestionTests(PortalTicketMachinesTests):
    # Each machine card asks one thing first — in their list, or new — and
    # only that answer's fields count.

    def test_the_answer_decides_what_is_needed(self):
        self.client.login(username='fitnessfirst', password='pass12345')
        page = self.client.get('/customers/portal/tickets/new/')
        self.assertContains(page, 'Is this machine in your list?')

        # "In my list" but nothing picked: asked to pick, nothing about a serial.
        response = self.client.post('/customers/portal/tickets/new/', self._payload(machines=[
            {'which': 'list', 'model_name': 'typed by mistake', 'problem': 'display'},
        ]))
        self.assertContains(response, 'Please choose the machine from your list.')
        self.assertNotContains(response, 'Please enter the serial number')

        # "New": model and serial; a machine picked earlier is dropped.
        self.client.post('/customers/portal/tickets/new/', self._payload(machines=[
            {'which': 'new', 'asset': str(self.asset.pk), 'model_name': 'Old bike', 'serial_no': 'B-9', 'problem': 'display'},
        ]))
        machine = CustomerTicket.objects.get().machines.get()
        self.assertEqual((machine.asset, machine.model_name, machine.serial_no), (None, 'Old bike', 'B-9'))

    def test_a_customer_with_no_machines_is_not_asked(self):
        from customers.models import Asset
        Asset.objects.filter(site__customer=self.customer).delete()
        self.client.login(username='fitnessfirst', password='pass12345')
        self.assertNotContains(self.client.get('/customers/portal/tickets/new/'), 'Is this machine in your list?')
