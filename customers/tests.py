from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from people.models import Technician
from reference.models import Country
from tasks.models import CustomerTicket

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

    def test_supervisor_creates_a_customer(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/customers/new/', {'name': 'Gold Gym', 'segment': 'gym'})
        self.assertEqual(response.status_code, 302)

        customer = Customer.objects.get(name='Gold Gym')
        self.assertEqual(customer.country, self.country)
        self.assertEqual(customer.segment, 'gym')

    def test_hotel_segment_works(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post('/customers/new/', {'name': 'Grand Hotel', 'segment': 'hotel'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Customer.objects.filter(name='Grand Hotel', segment='hotel').exists())


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

    def test_supervisor_adds_a_site(self):
        self.client.login(username='supervisor1', password='pass12345')
        response = self.client.post(f'/customers/{self.customer.pk}/', {
            'name': 'JBR Branch', 'address': 'JBR', 'contact_name': '', 'contact_phone': '',
            'contact_email': '', 'access_notes': '',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Site.objects.filter(customer=self.customer, name='JBR Branch').exists())

    def test_duplicate_site_name_is_rejected(self):
        self.client.login(username='supervisor1', password='pass12345')
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
        response = self.client.post(self.url, {
            'action': 'create_login', 'username': 'fitnessfirst', 'password1': 'r4nd0m-Pass!',
            'password2': 'r4nd0m-Pass!',
        })
        self.assertEqual(response.status_code, 200)
        self.customer.refresh_from_db()
        self.assertIsNone(self.customer.user_id)

    def test_manager_can_create_a_login(self):
        self.client.login(username='manager1', password='pass12345')
        response = self.client.post(self.url, {
            'action': 'create_login', 'username': 'fitnessfirst', 'password1': 'r4nd0m-Pass!',
            'password2': 'r4nd0m-Pass!',
        })
        self.assertEqual(response.status_code, 302)
        self.customer.refresh_from_db()
        self.assertIsNotNone(self.customer.user_id)
        self.assertEqual(self.customer.user.username, 'fitnessfirst')

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

    def test_customer_cannot_submit_a_ticket_for_another_customers_site(self):
        self.client.login(username='fitnessfirst', password='pass12345')
        response = self.client.post('/customers/portal/tickets/new/', self._payload(site=self.other_site.pk))
        self.assertEqual(response.status_code, 200)
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
