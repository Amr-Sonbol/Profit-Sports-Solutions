"""The audit trail's retention rule: entries older than --keep-days (two years by
default) are removed. Run it monthly (the scheduled job, docs/go_live_plan.md).

    python manage.py prune_audit --keep-days 730
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from people.models import AuditEntry


class Command(BaseCommand):
    help = 'Remove audit entries older than --keep-days (default 730).'

    def add_arguments(self, parser):
        parser.add_argument('--keep-days', type=int, default=730)

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options['keep_days'])
        count, _ = AuditEntry.objects.filter(at__lt=cutoff).delete()
        self.stdout.write(f'{count} audit entries older than {options["keep_days"]} days removed.')
