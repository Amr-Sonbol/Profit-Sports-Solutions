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

    def test_an_every_country_role_reaches_a_customer_in_another_country(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        other_customer = Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')

        # Every country is the Project Manager's (and Operations, Technical Support, Admin) — a Manager has
        # one country and is refused (docs/access_rules.md).
        self.client.login(username='manager1', password='pass12345')
        self.assertIn(self.client.get(f'/customers/{other_customer.pk}/edit/').status_code, (403, 404))
        Technician.objects.filter(user__username='manager1').update(role=Technician.Role.PROJECT_MANAGER)
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

    def test_every_country_roles_see_customers_from_every_country(self):
        other_country = Country.objects.create(
            name='Egypt', iso_code='EG', timezone='Africa/Cairo', currency_code='EGP',
        )
        Customer.objects.create(country=other_country, name='Cairo Gym', segment='gym')

        # Every country is the Project Manager's (and Operations, Technical Support, Admin) — a Manager has
        # one country and is refused (docs/access_rules.md).
        self.client.login(username='manager1', password='pass12345')
        self.assertIn(self.client.get('/customers/all/').status_code, (403, 404))
        Technician.objects.filter(user__username='manager1').update(role=Technician.Role.PROJECT_MANAGER)
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
        # The same message as a wrong password: it never says the account exists.
        self.assertContains(response, 'The email or password is incorrect.')
        self.assertNotContains(response, 'customer account')

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
        response = self.client.get('/customers/portal/requests/')
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
        response = self.client.get('/customers/portal/requests/')
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
        self.assertContains(self.client.get('/customers/portal/reports/'), self.url)
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


class PortalProgressTests(CustomerTestCase):
    # The portal home in the customer's words: the next visit up front,
    # progress as Received → Being arranged → Visit booked → Done.

    def setUp(self):
        super().setUp()
        self.customer.user = User.objects.create_user('fitnessfirst', password='pass12345')
        self.customer.save(update_fields=['user'])
        self.client.login(username='fitnessfirst', password='pass12345')

    def _visit(self, number, status='assigned', **dates):
        from tasks.models import Task
        return Task.objects.create(
            task_number=number, site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CONTRACT, reported_at=timezone.now(), created_by=self.supervisor_user,
            status=status, **dates,
        )

    def test_the_next_visit_is_the_soonest_booked_day_still_ahead(self):
        from datetime import timedelta
        self._visit('AE-0901', scheduled_for=timezone.now() + timedelta(days=9))
        self._visit('AE-0902', scheduled_for=timezone.now() + timedelta(days=2))
        self._visit('AE-0903')  # no date yet
        page = self.client.get('/customers/portal/')
        self.assertEqual(page.context['next_visit'].task_number, 'AE-0902')
        self.assertContains(page, 'Your next visit')
        self.assertContains(page, 'Visit scheduled on')
        visits = self.client.get('/customers/portal/visits/')
        self.assertEqual([v.task_number for v in visits.context['visits']], ['AE-0902', 'AE-0901', 'AE-0903'])
        self.assertContains(visits, 'Date to be set')
        # The team's words never reach the customer.
        self.assertNotContains(page, 'Assigned')
        self.assertNotContains(visits, 'Assigned')

    def test_a_booked_day_that_passed_is_rescheduling(self):
        from datetime import timedelta
        self._visit('AE-0904', scheduled_for=timezone.now() - timedelta(days=30))
        page = self.client.get('/customers/portal/')
        self.assertIsNone(page.context['next_visit'])
        visits = self.client.get('/customers/portal/visits/')
        self.assertNotContains(visits, 'Visit scheduled')
        self.assertContains(visits, 'Rescheduling')

    def test_done_visits_are_history_with_their_report(self):
        from reports.models import WorkReport
        visit = self._visit('AE-0905', status='closed', scheduled_for=timezone.now())
        WorkReport.objects.create(
            task=visit, findings='Belt worn out', action_taken='Replaced belt', resolved=True,
            labour_hours='1.50', customer_name='Ali', submitted_at=timezone.now(),
        )
        # Home counts what's going on, not reports: a new one reaches them through the bell.
        self.assertNotIn('report_count', self.client.get('/customers/portal/').context)
        page = self.client.get('/customers/portal/visits/?show=past')
        self.assertEqual([v.task_number for v in page.context['visits']], ['AE-0905'])
        self.assertContains(page, f'/customers/portal/visits/{visit.pk}/report.pdf')
        self.assertContains(page, 'Done')
        self.assertContains(self.client.get(f'/customers/portal/visits/{visit.pk}/'), '?show=past')

    def test_a_ticket_reads_as_its_visit_does(self):
        from datetime import timedelta
        visit = self._visit('AE-0906', scheduled_for=timezone.now() + timedelta(days=3))
        ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0009', customer=self.customer, company_name='Fitness First',
            site_description='Marina Branch', site_address='Dubai Marina', contact_name='Ali',
            contact_phone='0501234567', description='Belt noise', submitted_at=timezone.now(),
            status=CustomerTicket.Status.CONVERTED, task=visit,
        )
        self.assertContains(self.client.get('/customers/portal/requests/'), 'Visit scheduled on')
        page = self.client.get(f'/tasks/tickets/status/{ticket.token}/')
        self.assertContains(page, 'Visit scheduled on')
        self.assertContains(page, 'A technician is scheduled to visit on')

    def test_no_back_link_on_the_portal_home_or_login(self):
        self.assertNotContains(self.client.get('/customers/portal/'), 'page-back')
        self.assertContains(self.client.get('/customers/portal/tickets/new/'), 'Back to requests')
        for page in ('requests/', 'visits/', 'reports/', 'machines/', 'settings/'):
            self.assertNotContains(self.client.get(f'/customers/portal/{page}'), 'page-back')
        self.client.logout()
        page = self.client.get('/customers/portal/login/')
        self.assertNotContains(page, 'page-back')
        self.assertContains(page, '>Email<')

    def test_a_customer_with_one_site_has_it_chosen_already(self):
        form = self.client.get('/customers/portal/tickets/new/').context['form']
        self.assertEqual(form.fields['site'].initial, self.site.pk)
        Site.objects.create(customer=self.customer, name='JBR Branch', address='JBR')
        form = self.client.get('/customers/portal/tickets/new/').context['form']
        self.assertIsNone(form.fields['site'].initial)


class PortalVisitPageTests(CustomerTestCase):
    # A page per visit: when, the request it came from, and once reported,
    # what the customer signed — without the hours.

    def setUp(self):
        super().setUp()
        from reference.models import Brand
        from tasks.models import Task
        self.customer.user = User.objects.create_user('fitnessfirst', password='pass12345')
        self.customer.save(update_fields=['user'])
        self.machine = Asset.objects.create(
            site=self.site, brand=Brand.objects.create(name='Technogym'), model_name='Excite Run 700', serial_no='TG-77',
        )
        self.visit = Task.objects.create(
            task_number='AE-0960', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CONTRACT, reported_at=timezone.now(), created_by=self.supervisor_user,
            status=Task.Status.ASSIGNED,
        )
        self.url = f'/customers/portal/visits/{self.visit.pk}/'
        self.client.login(username='fitnessfirst', password='pass12345')

    def test_before_the_visit_it_says_when_and_what_it_came_from(self):
        ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0060', customer=self.customer, company_name='Fitness First',
            site_description='Marina Branch', site_address='Dubai Marina', contact_name='Ali',
            contact_phone='0501234567', description='Noise', submitted_at=timezone.now(),
            status=CustomerTicket.Status.CONVERTED, task=self.visit,
        )
        page = self.client.get(self.url)
        self.assertContains(page, 'Date to be set')
        self.assertContains(page, ticket.ticket_number)
        self.assertContains(page, 'The visit report appears here')
        self.assertContains(self.client.get('/customers/portal/visits/'), self.url)

    def test_after_the_visit_it_shows_what_was_signed_but_not_the_hours(self):
        from reports.models import PartUsed, WorkReport
        from tasks.models import Task, TaskAsset
        self.visit.status = Task.Status.CLOSED
        self.visit.save(update_fields=['status'])
        TaskAsset.objects.create(
            task=self.visit, asset=self.machine, outcome=TaskAsset.Outcome.REPAIRED, finding_code='belt',
            action_code='belt_replaced',
        )
        report = WorkReport.objects.create(
            task=self.visit, findings='Belt worn out', action_taken='Cleaned the motor bay', resolved=True,
            labour_hours='3.25', customer_name='Ali Manager', submitted_at=timezone.now(),
            work_lines=['bolts'], recommendations=['regular_ppm'],
        )
        PartUsed.objects.create(report=report, part_code='BELT-01', description='Running belt', quantity=1)
        page = self.client.get(self.url)
        for text in ('Belt worn out', 'Cleaned the motor bay', 'All bolts checked and tightened.',
                     'Worn or slipping belt', 'Belt replaced', 'Repaired', 'BELT-01', 'Ali Manager',
                     'We recommend keeping to the scheduled preventive maintenance visits.',
                     f'/customers/portal/visits/{self.visit.pk}/report.pdf'):
            self.assertContains(page, text)
        self.assertNotContains(page, '3.25')
        self.assertContains(page, 'Done')

    def test_the_request_page_links_each_visit_it_became_and_its_report(self):
        from reports.models import WorkReport
        from tasks.models import Task, TicketMachine
        ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0061', customer=self.customer, company_name='Fitness First',
            site_description='Marina Branch', site_address='Dubai Marina', contact_name='Ali',
            contact_phone='0501234567', description='Two machines', submitted_at=timezone.now(),
            status=CustomerTicket.Status.CONVERTED, task=self.visit,
        )
        # Its second machine went to a visit of its own, already done.
        second = Task.objects.create(
            task_number='AE-0962', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CONTRACT, reported_at=timezone.now(), created_by=self.supervisor_user,
            status=Task.Status.CLOSED,
        )
        WorkReport.objects.create(
            task=second, findings='Display loose', action_taken='Fixed', resolved=True, labour_hours='1.00',
            customer_name='Ali', submitted_at=timezone.now(),
        )
        TicketMachine.objects.create(ticket=ticket, position=0, model_name='Bike', problem='display', description='x', task=self.visit)
        TicketMachine.objects.create(ticket=ticket, position=1, model_name='Rower', problem='noise', description='y', task=second)
        page = self.client.get(f'/tasks/tickets/status/{ticket.token}/')
        self.assertContains(page, 'Your visits')
        self.assertContains(page, f'/customers/portal/visits/{self.visit.pk}/')
        self.assertContains(page, f'/customers/portal/visits/{second.pk}/report.pdf')

    def _finish(self):
        from reports.models import WorkReport
        from tasks.models import Task
        self.visit.status = Task.Status.CLOSED
        self.visit.save(update_fields=['status'])
        WorkReport.objects.create(
            task=self.visit, findings='Belt worn', action_taken='Replaced', resolved=True, labour_hours='1.00',
            customer_name='Ali', submitted_at=timezone.now(),
        )

    def test_a_finished_visit_can_be_rated_once(self):
        from reports.models import CustomerFeedback
        self.assertNotContains(self.client.get(self.url), 'How was this visit?')  # not done yet
        self._finish()
        self.assertContains(self.client.get('/customers/portal/reports/'), 'Rate this visit')
        response = self.client.post(self.url, {'rating': '5', 'comment': 'Quick and tidy'})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        feedback = CustomerFeedback.objects.get(task=self.visit)
        self.assertEqual((feedback.rating, feedback.comment, feedback.requested_by), (5, 'Quick and tidy', None))
        page = self.client.get(self.url)
        self.assertContains(page, 'You rated it 5 out of 5.')
        self.assertNotContains(self.client.get('/customers/portal/reports/'), 'Rate this visit')
        self.client.post(self.url, {'rating': '1'})  # no second go
        feedback.refresh_from_db()
        self.assertEqual(feedback.rating, 5)

    def test_rating_in_the_portal_answers_an_emailed_request(self):
        from reports.models import CustomerFeedback
        self._finish()
        requested = CustomerFeedback.objects.create(task=self.visit, requested_at=timezone.now(), requested_by=self.admin_user)
        self.client.post(self.url, {'rating': '4'})
        requested.refresh_from_db()
        self.assertEqual((requested.rating, requested.requested_by), (4, self.admin_user))
        self.assertIsNotNone(requested.submitted_at)

    def test_someone_elses_visit_is_not_found(self):
        from tasks.models import Task
        other = Customer.objects.create(country=self.country, name='Other Gym', segment='gym')
        theirs = Task.objects.create(
            task_number='AE-0961', site=Site.objects.create(customer=other, name='Far', address='x'),
            priority=Task.Priority.NORMAL, source=Task.Source.PHONE, billing_type=Task.BillingType.CONTRACT,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.ASSIGNED,
        )
        self.assertEqual(self.client.get(f'/customers/portal/visits/{theirs.pk}/').status_code, 404)


class PortalSettingsTests(CustomerTestCase):
    # A customer keeps their own details, language and password up to date.

    def setUp(self):
        super().setUp()
        self.customer.user = User.objects.create_user('ali@fitnessfirst.ae', email='ali@fitnessfirst.ae', password='pass12345')
        self.customer.save(update_fields=['user'])
        self.client.login(username='ali@fitnessfirst.ae', password='pass12345')

    def test_details_and_language_are_saved(self):
        response = self.client.post('/customers/portal/settings/', {
            'action': 'details', 'contact_name': 'Sara', 'contact_phone': '0509999999',
            'contact_email': 'sara@fitnessfirst.ae', 'billing_address': 'Dubai Marina', 'shipping_address': 'Al Quoz',
            'language': 'ar',
        })
        self.assertRedirects(response, '/customers/portal/settings/', fetch_redirect_response=False)
        self.customer.refresh_from_db()
        self.assertEqual((self.customer.contact_name, self.customer.contact_email, self.customer.language),
                         ('Sara', 'sara@fitnessfirst.ae', 'ar'))

    def test_the_password_needs_the_current_one_and_says_it_changed(self):
        new = {'action': 'password', 'new_password1': 'Gym-Belt-2026!', 'new_password2': 'Gym-Belt-2026!'}
        response = self.client.post('/customers/portal/settings/', {**new, 'old_password': 'wrong'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.get(username='ali@fitnessfirst.ae').check_password('Gym-Belt-2026!'))
        response = self.client.post('/customers/portal/settings/', {**new, 'old_password': 'pass12345'})
        self.assertRedirects(response, '/customers/portal/settings/', fetch_redirect_response=False)
        self.assertTrue(User.objects.get(username='ali@fitnessfirst.ae').check_password('Gym-Belt-2026!'))
        self.assertEqual(len(mail.outbox), 1)
        # Still signed in on this device.
        self.assertEqual(self.client.get('/customers/portal/settings/').status_code, 200)

    def test_the_login_email_is_shown_not_editable(self):
        page = self.client.get('/customers/portal/settings/')
        self.assertContains(page, 'ali@fitnessfirst.ae</strong>')
        self.assertNotIn('username', page.context['details_form'].fields)

    def test_staff_and_unfinished_accounts_cannot_use_it(self):
        self.customer.must_change_password = True
        self.customer.save(update_fields=['must_change_password'])
        self.assertRedirects(
            self.client.get('/customers/portal/settings/'), '/customers/portal/first-login/', fetch_redirect_response=False,
        )
        self.client.login(username='supervisor1', password='pass12345')
        self.assertIn(self.client.get('/customers/portal/settings/').status_code, (302, 403))


class PortalBellTests(CustomerTestCase):
    # The customer's bell: a report ready to rate, our team's reply, a visit
    # booked — each cleared once they open what it's about.

    def setUp(self):
        super().setUp()
        from tasks.models import Task
        self.customer.user = User.objects.create_user('fitnessfirst', password='pass12345')
        self.customer.save(update_fields=['user'])
        self.visit = Task.objects.create(
            task_number='AE-0970', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CONTRACT, reported_at=timezone.now(), created_by=self.supervisor_user,
            status=Task.Status.ASSIGNED, scheduled_date=timezone.localdate(),
        )
        self.ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0070', customer=self.customer, company_name='Fitness First',
            site_description='Marina Branch', site_address='Dubai Marina', contact_name='Ali',
            contact_phone='0501234567', description='Noise', submitted_at=timezone.now(),
        )
        self.client.login(username='fitnessfirst', password='pass12345')

    def test_each_event_rings_the_bell_once_and_opening_it_clears_it(self):
        from .models import CustomerNotification
        from . import notify
        notify.visit_booked(self.visit)
        notify.visit_booked(self.visit)  # booked again: still one item
        notify.report_ready(self.visit)
        notify.staff_replied(self.ticket)
        page = self.client.get('/customers/portal/')
        self.assertEqual(len(page.context['portal_bell']), 3)
        self.assertContains(page, 'notif-bell__badge')
        self.assertContains(page, 'New reply from our team')
        self.assertContains(page, 'Your visit report is ready — please rate the visit')
        self.client.get(f'/tasks/tickets/status/{self.ticket.token}/')
        self.client.get(f'/customers/portal/visits/{self.visit.pk}/')
        self.assertFalse(CustomerNotification.objects.filter(seen_at__isnull=True).exists())
        page = self.client.get('/customers/portal/')
        self.assertNotContains(page, 'notif-bell__badge')
        # Still in the list, just no longer new.
        self.assertEqual([item['new'] for item in page.context['portal_bell']], [False] * 3)

    def test_our_own_account_and_customers_without_a_login_get_nothing(self):
        from tasks.models import Task
        from .models import CustomerNotification
        from . import notify
        ours = Customer.objects.create(country=self.country, name='Profit Sports Solutions', segment='other', is_internal=True)
        warehouse_visit = Task.objects.create(
            task_number='AE-0971', site=Site.objects.create(customer=ours, name='Warehouse', address='x'),
            priority=Task.Priority.NORMAL, source=Task.Source.INTERNAL, billing_type=Task.BillingType.CONTRACT,
            reported_at=timezone.now(), created_by=self.supervisor_user, status=Task.Status.ASSIGNED,
            scheduled_date=timezone.localdate(),
        )
        notify.visit_booked(warehouse_visit)
        other = Customer.objects.create(country=self.country, name='No Login Gym', segment='gym')
        notify.staff_replied(CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0071', customer=other, company_name='No Login Gym',
            site_description='x', site_address='x', contact_name='x', contact_phone='x', description='x',
            submitted_at=timezone.now(),
        ))
        self.assertFalse(CustomerNotification.objects.exists())

    def test_our_teams_reply_rings_it(self):
        from .models import CustomerNotification
        manager = User.objects.create_user('support1', password='pass12345')
        Technician.objects.create(
            user=manager, country=self.country, full_name='Sam Support', language='en',
            role=Technician.Role.SUPPORT_MANAGER, employment_type='staff',
        )
        self.client.login(username='support1', password='pass12345')
        self.client.post(f'/tasks/tickets/{self.ticket.pk}/', {'action': 'add_reply', 'message': 'We are on it.'})
        self.assertTrue(CustomerNotification.objects.filter(kind='reply', ticket=self.ticket).exists())

    def test_requests_are_in_open_and_closed_tabs_with_their_progress(self):
        CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0072', customer=self.customer, company_name='Fitness First',
            site_description='Marina Branch', site_address='Dubai Marina', contact_name='Ali',
            contact_phone='0501234567', description='Old', submitted_at=timezone.now(),
            status=CustomerTicket.Status.CLOSED,
        )
        open_page = self.client.get('/customers/portal/requests/')
        self.assertEqual([t.ticket_number for t in open_page.context['tickets']], ['AE-T0070'])
        self.assertIsNotNone(open_page.context['tickets'][0].progress)
        closed_page = self.client.get('/customers/portal/requests/?show=closed')
        self.assertEqual([t.ticket_number for t in closed_page.context['tickets']], ['AE-T0072'])

    def test_the_progress_bar_follows_the_words(self):
        from .progress import CLOSED, DONE, RECEIVED, VISIT_BOOKED, progress_steps
        self.assertEqual([state for _label, state in progress_steps(RECEIVED)], ['current', 'todo', 'todo', 'todo'])
        self.assertEqual([state for _label, state in progress_steps(VISIT_BOOKED)], ['done', 'done', 'current', 'todo'])
        self.assertEqual([state for _label, state in progress_steps(DONE)], ['done'] * 4)
        self.assertIsNone(progress_steps(CLOSED))


class CustomerAddsMachineTests(CustomerTestCase):
    # A customer adds a machine; it waits for confirmation — by the office
    # from the sticker photo, or by a technician recording it at a visit.

    def setUp(self):
        super().setUp()
        from reference.models import Brand
        from people.models import RolePermission
        self.brand = Brand.objects.create(name='Technogym')
        self.customer.user = User.objects.create_user('fitnessfirst', password='pass12345')
        self.customer.save(update_fields=['user'])
        for permission in ('manage_customers', 'view_machines'):
            RolePermission.objects.update_or_create(role='admin', permission=permission, defaults={'allowed': True})
        self.client.login(username='fitnessfirst', password='pass12345')

    def _add(self, **overrides):
        return self.client.post('/customers/portal/machines/add/', {
            'site': self.site.pk, 'brand': self.brand.pk, 'model_name': 'Excite Run 700', 'serial_no': ' TG-77 ',
            'code': 'EXC-700', **overrides,
        })

    def test_an_added_machine_waits_for_confirmation(self):
        response = self._add()
        machine = Asset.objects.get()
        self.assertRedirects(response, f'/customers/portal/machines/{machine.pk}/', fetch_redirect_response=False)
        self.assertEqual((machine.status, machine.serial_no, machine.code), ('pending', 'TG-77', 'EXC-700'))
        self.assertContains(self.client.get('/customers/portal/machines/'), 'Waiting for confirmation')
        # They can already report a problem with it.
        form = self.client.get(f'/customers/portal/tickets/new/?machine={machine.pk}').context['machines'][0]
        self.assertEqual(form.initial['asset'], str(machine.pk))

    def test_the_same_serial_twice_or_someone_elses_site_is_refused(self):
        self._add()
        self.assertContains(self._add(), 'This machine is already in your list.')
        other = Customer.objects.create(country=self.country, name='Other Gym', segment='gym')
        far = Site.objects.create(customer=other, name='Far', address='x')
        response = self._add(site=far.pk, serial_no='OTHER-1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Asset.objects.count(), 1)

    def test_the_office_sees_it_and_confirms_or_removes_it(self):
        self._add()
        self._add(serial_no='TG-78')
        first, second = Asset.objects.order_by('pk')
        self.client.login(username='admin1', password='pass12345')
        home = self.client.get('/')
        self.assertTrue(any(item['count'] == 2 for item in home.context['actions']))
        listed = self.client.get('/tasks/machines/?status=pending')
        self.assertEqual([a.pk for a in listed.context['page_obj']], [first.pk, second.pk])
        self.client.post(f'/tasks/machines/{first.pk}/', {'action': 'confirm'})
        first.refresh_from_db()
        self.assertEqual(first.status, 'active')
        self.client.post(f'/tasks/machines/{second.pk}/', {'action': 'remove'})
        self.assertFalse(Asset.objects.filter(pk=second.pk).exists())

    def test_only_whoever_manages_customers_confirms(self):
        from people.models import RolePermission
        self._add()
        machine = Asset.objects.get()
        RolePermission.objects.filter(role='admin', permission='manage_customers').update(allowed=False)
        self.client.login(username='admin1', password='pass12345')
        self.assertNotContains(self.client.get(f'/tasks/machines/{machine.pk}/'), 'Confirm the machine')
        self.client.post(f'/tasks/machines/{machine.pk}/', {'action': 'confirm'})
        machine.refresh_from_db()
        self.assertEqual(machine.status, 'pending')


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
            # What it's about and what they need: machines with a problem, unless a test says otherwise.
            'about': 'machines', 'need': 'repair',
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

    def test_submitting_notifies_the_country_support_manager_by_email(self):
        # A new ticket goes to the Operations Manager and the country's
        # Country Support Manager (people.NotificationSettings.new_ticket_roles).
        support_user = User.objects.create_user('support1', email='sara@example.com', password='pass12345')
        Technician.objects.create(
            user=support_user, country=self.country, full_name='Sara Support',
            language='en', role=Technician.Role.COUNTRY_SUPPORT, employment_type='staff',
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
            {'asset': str(self.asset.pk), 'problem': 'not_working'},
            {'model_name': 'Old bike', 'serial_no': 'B-9', 'code': 'BK-9', 'problem': 'display'},
        ]))
        ticket = CustomerTicket.objects.get()
        first, second = ticket.machines.all()
        self.assertEqual((first.product_code, second.product_code), ('', 'BK-9'))
        self.assertEqual((first.asset, first.machine_name, first.serial),
                         (self.asset, 'Technogym Excite Run 700', 'TG-77'))
        self.assertEqual((second.asset, second.machine_name, second.serial), (None, 'Old bike', 'B-9'))
        # The ticket's text fields are written from the machines for everything that reads them.
        self.assertEqual(ticket.serial_numbers, 'TG-77' + chr(10) + 'B-9')
        self.assertIn('Technogym Excite Run 700 (TG-77): Not working', ticket.description)

    def test_reporting_from_a_machines_page_has_it_chosen_and_sends(self):
        self.client.login(username='fitnessfirst', password='pass12345')
        page = self.client.get(f'/customers/portal/tickets/new/?machine={self.asset.pk}')
        self.assertEqual(page.context['form'].fields['site'].initial, self.site.pk)
        first = page.context['machines'][0]
        self.assertEqual((first.initial['which'], first.initial['asset']), ('list', str(self.asset.pk)))
        # As the page posts it: that first card is an "initial" form now.
        response = self.client.post('/customers/portal/tickets/new/', self._payload(
            machines=[{'which': 'list', 'asset': str(self.asset.pk), 'problem': 'not_working'}],
            **{'machines-INITIAL_FORMS': '1'},
        ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CustomerTicket.objects.get().machines.get().asset, self.asset)

    def test_someone_elses_machine_is_not_chosen(self):
        theirs = Asset.objects.create(site=self.other_site, brand=self.brand, model_name='Their bike')
        self.client.login(username='fitnessfirst', password='pass12345')
        page = self.client.get(f'/customers/portal/tickets/new/?machine={theirs.pk}')
        self.assertFalse(page.context['machines'][0].initial)


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


class PortalMachinesTests(CustomerTestCase):
    # "My machines": each machine at the customer's sites, its history, and
    # reporting a problem with it.

    def setUp(self):
        super().setUp()
        from reference.models import Brand
        self.customer.user = User.objects.create_user('fitnessfirst', password='pass12345')
        self.customer.save(update_fields=['user'])
        self.brand = Brand.objects.create(name='Technogym')
        self.machine = Asset.objects.create(
            site=self.site, brand=self.brand, model_name='Excite Run 700', serial_no='TG-77', code='EXC-RUN-700',
        )
        self.client.login(username='fitnessfirst', password='pass12345')

    def test_the_list_shows_this_customers_machines_but_not_retired_ones(self):
        Asset.objects.create(site=self.site, brand=self.brand, model_name='Old bike', status=Asset.Status.RETIRED)
        other = Customer.objects.create(country=self.country, name='Other Gym', segment='gym')
        Asset.objects.create(
            site=Site.objects.create(customer=other, name='Far', address='x'), brand=self.brand, model_name='Their rower',
        )
        page = self.client.get('/customers/portal/machines/')
        self.assertContains(page, 'Excite Run 700')
        self.assertNotContains(page, 'Old bike')
        self.assertNotContains(page, 'Their rower')
        self.assertContains(page, f'/customers/portal/machines/{self.machine.pk}/')

    def test_a_machine_shows_its_code_visits_and_open_requests(self):
        from reports.models import WorkReport
        from tasks.models import Task, TaskAsset, TicketMachine
        visit = Task.objects.create(
            task_number='AE-0950', site=self.site, priority=Task.Priority.NORMAL, source=Task.Source.PHONE,
            billing_type=Task.BillingType.CONTRACT, reported_at=timezone.now(), created_by=self.supervisor_user,
            status=Task.Status.CLOSED, scheduled_for=timezone.now(),
        )
        TaskAsset.objects.create(task=visit, asset=self.machine, outcome=TaskAsset.Outcome.REPAIRED)
        WorkReport.objects.create(
            task=visit, findings='Belt worn', action_taken='Replaced belt', resolved=True, labour_hours='1.00',
            customer_name='Ali', submitted_at=timezone.now(),
        )
        ticket = CustomerTicket.objects.create(
            country=self.country, ticket_number='AE-T0050', customer=self.customer, company_name='Fitness First',
            site_description='Marina Branch', site_address='Dubai Marina', contact_name='Ali',
            contact_phone='0501234567', description='Noise', submitted_at=timezone.now(),
        )
        TicketMachine.objects.create(ticket=ticket, asset=self.machine, position=0, problem='noise', description='Noise')
        page = self.client.get(f'/customers/portal/machines/{self.machine.pk}/')
        self.assertContains(page, 'EXC-RUN-700')
        self.assertContains(page, 'Repaired')
        self.assertContains(page, f'/customers/portal/visits/{visit.pk}/report.pdf')
        self.assertContains(page, 'AE-T0050')
        self.assertContains(page, f'/customers/portal/tickets/new/?machine={self.machine.pk}')
        self.assertEqual(self.client.get('/customers/portal/machines/').context['sites'].__class__.__name__, 'dict_items')

    def test_someone_elses_machine_is_not_found(self):
        other = Customer.objects.create(country=self.country, name='Other Gym', segment='gym')
        theirs = Asset.objects.create(
            site=Site.objects.create(customer=other, name='Far', address='x'), brand=self.brand, model_name='Rower',
        )
        self.assertEqual(self.client.get(f'/customers/portal/machines/{theirs.pk}/').status_code, 404)

    def test_staff_cannot_open_the_portal_machines(self):
        self.client.login(username='supervisor1', password='pass12345')
        self.assertIn(self.client.get('/customers/portal/machines/').status_code, (302, 403))


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
        self.assertContains(page, 'Which machine?')

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
