"""Copies every uploaded file from the disk (MEDIA_ROOT) to where uploads now live
(Cloudflare R2, once R2_BUCKET is set) — once, when moving to R2; safe to run
again (a file already there is skipped). Addresses don't change (/media/<path>),
so nothing in the database needs touching. Then the disk can go (render.yaml).

    python manage.py copy_media_to_storage
"""
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Copy the uploads on disk to the configured storage (R2).'

    def handle(self, *args, **options):
        if not hasattr(default_storage, 'signed_url'):
            raise CommandError('Uploads are still on disk: set R2_BUCKET and the R2 keys first.')
        root = Path(settings.MEDIA_ROOT)
        copied = skipped = 0
        for source in sorted(p for p in root.rglob('*') if p.is_file()):
            name = source.relative_to(root).as_posix()
            if default_storage.exists(name):
                skipped += 1
                continue
            with source.open('rb') as handle:
                saved = default_storage._save(name, File(handle))  # the same name, never renamed
            if saved != name:
                raise CommandError(f'{name} was stored as {saved}: stopped, nothing renamed.')
            copied += 1
        self.stdout.write(f'{copied} files copied, {skipped} already there.')
