"""Fill the local database with a large, realistic set of test data for load
testing and the monthly report, or remove it again (--delete).

Everything it makes is marked: customers are named "LOADTEST …", and task
and request numbers start with "LT" plus the country's prefix (LTUAE-000123,
LTUAE-T000045), which the real numbering (a count per "UAE-" prefix) never
counts, so real numbers carry on as if this data weren't there. It refuses
to run unless DEBUG is on, so it can't touch a live database.

    python manage.py loadtest_data                # the default volume
    python manage.py loadtest_data --customers 200 --tasks 3000 --requests 1500 --months 6
    python manage.py loadtest_data --delete
"""
import random
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from customers.models import Asset, Customer, Site
from reference.models import Brand, Country
from reports.models import CustomerFeedback, WorkReport
from people.models import Technician
from tasks.models import CustomerTicket, Task, TaskAssignment, TaskType

NAME_PREFIX = 'LOADTEST'
NUMBER_PREFIX = 'LT'
BATCH = 2000

# Where the work is: most customers in the UAE and Saudi Arabia.
COUNTRY_WEIGHTS = {'AE': 35, 'SA': 30, 'QA': 10, 'KW': 8, 'OM': 6, 'BH': 5, 'EG': 6}
GYM_WORDS = ['Fitness', 'Gym', 'Club', 'Wellness', 'Athletics', 'Training', 'Performance', 'Health']
CITY_WORDS = ['Marina', 'Downtown', 'Al Olaya', 'West Bay', 'Salmiya', 'Qurum', 'Juffair', 'Zamalek', 'Corniche']
MODELS = ['Run 700', 'Excite Bike', 'Skillmill', 'Element+ Bench', 'Synchro', 'Kinesis', 'Cross Trainer X',
          'Leg Press 500', 'Lat Pulldown', 'Rower Pro', 'Smith Machine', 'Cable Crossover']


class Command(BaseCommand):
    help = 'Generate (or --delete) a large set of marked test data for load testing and the monthly report.'

    def add_arguments(self, parser):
        parser.add_argument('--customers', type=int, default=1000)
        parser.add_argument('--sites', type=int, default=2500, help='branches, spread over the customers')
        parser.add_argument('--machines', type=int, default=10000)
        parser.add_argument('--tasks', type=int, default=20000)
        parser.add_argument('--requests', type=int, default=10000)
        parser.add_argument('--months', type=int, default=12, help='how far back the tasks and requests go')
        parser.add_argument('--seed', type=int, default=7, help='the same seed gives the same data')
        parser.add_argument('--delete', action='store_true', help='remove everything this command made, and stop')

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('Refusing to run: DEBUG is off, so this may be a live database.')
        if options['delete']:
            self._delete()
            return
        if Customer.objects.filter(name__startswith=NAME_PREFIX).exists():
            raise CommandError('Load-test data is already here. Remove it first with --delete.')
        self.random = random.Random(options['seed'])
        self.now = timezone.now()
        self.span = timedelta(days=30 * options['months'])
        with transaction.atomic():
            customers = self._customers(options['customers'])
            sites = self._sites(customers, options['sites'])
            self._machines(sites, options['machines'])
            tasks = self._tasks(sites, options['tasks'])
            self._assignments()
            self._visit_reports_and_ratings()
            self._requests(sites, tasks, options['requests'])
        self.stdout.write(self.style.SUCCESS('Done. Remove it all later with: python manage.py loadtest_data --delete'))

    # -- making it --------------------------------------------------------

    def _when(self):
        """A moment in the last --months, a little busier towards now."""
        return self.now - self.span * (self.random.random() ** 1.3)

    def _customers(self, count):
        countries = {c.iso_code: c for c in Country.objects.filter(iso_code__in=COUNTRY_WEIGHTS)}
        if not countries:
            raise CommandError('No Middle East countries found to put customers in.')
        codes = list(countries)
        weights = [COUNTRY_WEIGHTS[code] for code in codes]
        segments = [value for value, _label in Customer.Segment.choices]
        made = [
            Customer(
                country=countries[self.random.choices(codes, weights)[0]],
                name=f'{NAME_PREFIX} {self.random.choice(CITY_WORDS)} {self.random.choice(GYM_WORDS)} {number:04d}',
                segment=self.random.choice(segments),
                contact_name=f'Manager {number}', contact_phone=f'+97150{number:07d}',
            )
            for number in range(1, count + 1)
        ]
        Customer.objects.bulk_create(made, batch_size=BATCH)
        self.stdout.write(f'{count} customers')
        return list(Customer.objects.filter(name__startswith=NAME_PREFIX).select_related('country'))

    def _sites(self, customers, count):
        # Every customer gets one branch; the rest go to some of them (the chains).
        owners = customers + [self.random.choice(customers) for _ in range(max(count - len(customers), 0))]
        made = [
            Site(customer=owner, name=f'{self.random.choice(CITY_WORDS)} branch {number}',
                 address=f'{number} Test Street, {self.random.choice(CITY_WORDS)}')
            for number, owner in enumerate(owners, start=1)
        ]
        Site.objects.bulk_create(made, batch_size=BATCH)
        self.stdout.write(f'{len(made)} branches')
        return list(Site.objects.filter(customer__name__startswith=NAME_PREFIX).select_related('customer__country'))

    def _machines(self, sites, count):
        brands = list(Brand.objects.all())
        if not brands:
            raise CommandError('No brands found for the machines.')
        made = [
            Asset(site=self.random.choice(sites), brand=self.random.choice(brands),
                  model_name=self.random.choice(MODELS), serial_no=f'{NUMBER_PREFIX}-SN-{number:07d}')
            for number in range(1, count + 1)
        ]
        Asset.objects.bulk_create(made, batch_size=BATCH)
        self.stdout.write(f'{count} machines')

    def _task_status(self, reported_at):
        """Older work is mostly finished; this month's is still moving."""
        age = (self.now - reported_at).days
        S = Task.Status
        if age > 45:
            return self.random.choices([S.CLOSED, S.COMPLETED, S.CANCELLED], [80, 15, 5])[0]
        return self.random.choices(
            [S.NEW, S.ASSIGNED, S.ACCEPTED, S.IN_PROGRESS, S.PENDING_SUPERVISOR_REVIEW, S.COMPLETED, S.BLOCKED],
            [15, 25, 15, 10, 10, 20, 5],
        )[0]

    def _tasks(self, sites, count):
        creator = get_user_model().objects.filter(is_superuser=True).order_by('pk').first()
        if creator is None:
            raise CommandError('No superuser found to put down as the tasks\' creator.')
        types = list(TaskType.objects.all()) or [None]
        numbers = {}
        made = []
        for _ in range(count):
            site = self.random.choice(sites)
            country = site.customer.country
            prefix = f'{NUMBER_PREFIX}{country.task_prefix or country.iso_code}-'
            numbers[prefix] = numbers.get(prefix, 0) + 1
            reported_at = self._when()
            scheduled = reported_at + timedelta(days=self.random.randint(1, 14), hours=self.random.randint(0, 8))
            made.append(Task(
                task_number=f'{prefix}{numbers[prefix]:06d}', site=site, task_type=self.random.choice(types),
                priority=self.random.choices([v for v, _l in Task.Priority.choices], [15, 60, 20, 5])[0],
                source=self.random.choice([v for v, _l in Task.Source.choices]),
                billing_type=self.random.choice([v for v, _l in Task.BillingType.choices]),
                status=self._task_status(reported_at), reported_at=reported_at, created_by=creator,
                scheduled_for=scheduled if self.random.random() < 0.85 else None,
            ))
        Task.objects.bulk_create(made, batch_size=BATCH)
        self.stdout.write(f'{count} tasks')
        return list(Task.objects.filter(task_number__startswith=NUMBER_PREFIX).only('pk', 'site_id'))

    def _assignments(self):
        """Each task past "new" gets a lead technician from its own country
        (where there are any), and some a helper too. Countries with no
        technicians keep their work unassigned, as they would."""
        staff = {}
        for technician in Technician.objects.filter(is_active=True, role=Technician.Role.TECHNICIAN):
            staff.setdefault(technician.country_id, []).append(technician)
        made = []
        for task in Task.objects.filter(task_number__startswith=NUMBER_PREFIX).exclude(
            status=Task.Status.NEW,
        ).only('pk', 'reported_at', 'site__customer__country_id').select_related('site__customer'):
            people = staff.get(task.site.customer.country_id)
            if not people:
                continue
            crew = self.random.sample(people, min(len(people), 2 if self.random.random() < 0.2 else 1))
            for position, technician in enumerate(crew):
                made.append(TaskAssignment(
                    task=task, technician=technician, assigned_at=task.reported_at, is_active=True,
                    role=TaskAssignment.Role.LEAD if position == 0 else TaskAssignment.Role.HELPER,
                ))
        TaskAssignment.objects.bulk_create(made, batch_size=BATCH)
        self.stdout.write(f'{len(made)} technician assignments')

    def _visit_reports_and_ratings(self):
        """Finished tasks get their visit report (mostly resolved first time);
        most of those a customer rating, mostly good."""
        finished = Task.objects.filter(
            task_number__startswith=NUMBER_PREFIX,
            status__in=[Task.Status.PENDING_SUPERVISOR_REVIEW, Task.Status.COMPLETED, Task.Status.CLOSED],
        ).only('pk', 'reported_at', 'scheduled_for')
        reports, ratings = [], []
        for task in finished:
            visited = task.scheduled_for or task.reported_at + timedelta(days=2)
            submitted_at = min(visited + timedelta(hours=self.random.randint(1, 6)), self.now)
            reports.append(WorkReport(
                task=task, resolved=self.random.random() < 0.85, labour_hours=self.random.choice([1, 1.5, 2, 3, 4]),
                customer_name='Load test', findings='Load-test visit.', submitted_at=submitted_at,
            ))
            if self.random.random() < 0.6:
                rated_at = min(submitted_at + timedelta(hours=self.random.randint(2, 72)), self.now)
                ratings.append(CustomerFeedback(
                    task=task, token=secrets.token_urlsafe(32), requested_at=submitted_at,
                    rating=self.random.choices([5, 4, 3, 2, 1], [45, 30, 15, 6, 4])[0], submitted_at=rated_at,
                ))
        WorkReport.objects.bulk_create(reports, batch_size=BATCH)
        CustomerFeedback.objects.bulk_create(ratings, batch_size=BATCH)
        self.stdout.write(f'{len(reports)} visit reports, {len(ratings)} ratings')

    def _requests(self, sites, tasks, count):
        tasks_by_site = {}
        for task in tasks:
            tasks_by_site.setdefault(task.site_id, []).append(task)
        about = [v for v, _l in CustomerTicket.About.choices]
        need = [v for v, _l in CustomerTicket.Need.choices]
        S = CustomerTicket.Status
        numbers = {}
        made = []
        for _ in range(count):
            site = self.random.choice(sites)
            customer, country = site.customer, site.customer.country
            prefix = f'{NUMBER_PREFIX}{country.task_prefix or country.iso_code}-T'
            numbers[prefix] = numbers.get(prefix, 0) + 1
            submitted_at = self._when()
            status = self.random.choices([S.NEW, S.CONVERTED, S.CLOSED, S.DISMISSED], [15, 55, 25, 5])[0]
            site_tasks = tasks_by_site.get(site.pk)
            if status == S.CONVERTED and not site_tasks:
                status = S.CLOSED
            made.append(CustomerTicket(
                ticket_number=f'{prefix}{numbers[prefix]:06d}', token=secrets.token_urlsafe(32),
                country=country, customer=customer, site=site, company_name=customer.name,
                site_description=site.name, site_address=site.address,
                contact_name=customer.contact_name, contact_phone=customer.contact_phone,
                about=self.random.choices(about, [55, 15, 15, 8, 7])[0],
                need=self.random.choices(need, [55, 20, 15, 10])[0],
                description='Load-test request.', submitted_at=submitted_at, status=status,
                # A task belongs to one request at most: each is used once.
                task=site_tasks.pop(self.random.randrange(len(site_tasks))) if status == S.CONVERTED else None,
                close_reason='Resolved by phone.' if status == S.CLOSED else '',
            ))
        CustomerTicket.objects.bulk_create(made, batch_size=BATCH)
        self.stdout.write(f'{count} requests')

    # -- removing it ------------------------------------------------------

    def _delete(self):
        with transaction.atomic():
            requests, _ = CustomerTicket.objects.filter(ticket_number__startswith=NUMBER_PREFIX).delete()
            tasks, _ = Task.objects.filter(task_number__startswith=NUMBER_PREFIX).delete()
            machines, _ = Asset.objects.filter(site__customer__name__startswith=NAME_PREFIX).delete()
            sites, _ = Site.objects.filter(customer__name__startswith=NAME_PREFIX).delete()
            customers, _ = Customer.objects.filter(name__startswith=NAME_PREFIX).delete()
        self.stdout.write(self.style.SUCCESS(
            f'Removed the load-test data ({customers + sites + machines + tasks + requests} rows, '
            'including what hung off them).'
        ))
