from django.contrib.auth import get_user_model
from django.test import TestCase

from people.models import Technician
from reference.models import Country

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
