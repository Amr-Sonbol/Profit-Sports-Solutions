from django.core.management.base import BaseCommand

from tasks.views import send_visit_reminders


class Command(BaseCommand):
    help = "The day-before reminder to each customer with a visit tomorrow (run every hour)."

    def handle(self, *args, **options):
        self.stdout.write(f'{send_visit_reminders()} reminder(s) sent.')
