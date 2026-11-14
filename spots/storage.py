"""Uploaded files on Cloudflare R2 (settings: R2_BUCKET and friends), private.

A file's address is still the app's own /media/<path> — kept in the database,
in emails and in the app exactly as before — so every opening goes through the
permission check (spots.media.serve_media), which then hands the browser a
link to R2 signed for five minutes. Nobody can reach a file directly.
"""
from storages.backends.s3 import S3Storage

from django.conf import settings

SIGNED_FOR_SECONDS = 300


class PrivateMediaStorage(S3Storage):
    default_acl = None
    file_overwrite = False
    querystring_auth = True
    querystring_expire = SIGNED_FOR_SECONDS

    def url(self, name, parameters=None, expire=None, http_method=None):
        """The app's own address for the file (checked when opened), not R2's."""
        return '/' + settings.MEDIA_URL.strip('/') + '/' + name.lstrip('/')

    def signed_url(self, name, download=False):
        """R2's own link to the file, valid for five minutes — after the app's check.
        `download`: the browser saves it rather than showing it."""
        parameters = {'ResponseContentDisposition': 'attachment'} if download else None
        return super().url(name, parameters=parameters, expire=SIGNED_FOR_SECONDS)
