"""Uploaded files (photos, attachments, documents, signatures), served by
the app with a check on each one, on the live server as in development:

- staff (an active staff member signed in): any file;
- a customer signed in to the portal: only files from their own requests
  (attachments, serial photos, messages' files) and their own machines;
- a link signed by the app (signed_media_url): the mobile app's photos,
  which it loads without the website's sign-in; valid for a week;
- anyone else: not found.
"""
from urllib.parse import urlparse

from django.conf import settings
from django.core import signing
from django.http import Http404
from django.views.static import serve

SIGNED_FOR = 7 * 24 * 3600  # seconds
_signer = signing.TimestampSigner(salt='spots.media')


def _media_path(url):
    """The file's path under MEDIA_ROOT from its URL (absolute or not), or None."""
    path = urlparse(url).path
    prefix = '/' + settings.MEDIA_URL.strip('/') + '/'
    return path[len(prefix):] if path.startswith(prefix) else None


def signed_media_url(request, url):
    """An absolute link to a stored file that opens without signing in, for a
    week — for the mobile app. Anything that isn't a stored file is returned as is."""
    if not url:
        return ''
    path = _media_path(url)
    if path is None:
        return url
    token = _signer.sign(path)[len(path) + 1:]
    absolute = request.build_absolute_uri('/' + settings.MEDIA_URL.strip('/') + '/' + path)
    return f'{absolute}?t={token}'


def _signed(path, token):
    try:
        _signer.unsign(f'{path}:{token}', max_age=SIGNED_FOR)
        return True
    except signing.BadSignature:
        return False


def _customer_owns(customer, path):
    from customers.models import Asset
    from tasks.models import CustomerTicketAttachment, TicketMachine, TicketReply, TicketReplyFile

    return any(model.objects.filter(**{field: path, owner: customer}).exists() for model, field, owner in (
        (CustomerTicketAttachment, 'file', 'ticket__customer'),
        (TicketMachine, 'serial_photo', 'ticket__customer'),
        (TicketReply, 'attachment', 'ticket__customer'),
        (TicketReplyFile, 'file', 'reply__ticket__customer'),
        (Asset, 'sticker_photo', 'site__customer'),
    ))


def may_see(request, path):
    token = request.GET.get('t')
    if token and _signed(path, token):
        return True
    user = request.user
    if not user.is_authenticated:
        return False
    technician = getattr(user, 'technician', None)
    if technician is not None:
        return technician.is_active
    from customers.models import portal_customer

    customer = portal_customer(user)
    return customer is not None and _customer_owns(customer, path)


# An uploaded file can never act as a page of this site: nothing in it runs
# (no script, no form, no frame) and it loads nothing from elsewhere. Photos,
# videos and PDFs open in the browser to look at; anything else downloads.
UPLOAD_POLICY = "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; sandbox"
SHOWN_INLINE = ('image/', 'video/', 'application/pdf')


def serve_media(request, path):
    if '..' in path.split('/') or not may_see(request, path):
        raise Http404
    response = serve(request, path, document_root=settings.MEDIA_ROOT)
    # Shared and cached by nobody but the viewer's own browser.
    response['Cache-Control'] = 'private, max-age=3600'
    content_type = response.get('Content-Type', '')
    if content_type.startswith('application/pdf'):
        # The browser's own PDF viewer has to run, so no sandbox here: the policy still
        # lets the PDF load nothing and send nothing anywhere.
        response['Content-Security-Policy'] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    else:
        response['Content-Security-Policy'] = UPLOAD_POLICY
    if not content_type.startswith(SHOWN_INLINE):
        response['Content-Disposition'] = 'attachment'
    return response
