"""Push notifications to the mobile app, through Expo's push service
(docs: push_device). Best-effort, like email: never raises, never blocks
the action that triggered it for longer than the timeout.
"""
import json
import logging
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils import translation
from django.utils.translation import gettext as _

from .models import PushDevice

EXPO_PUSH_URL = 'https://exp.host/--/api/v2/push/send'
TIMEOUT_SECONDS = 5

logger = logging.getLogger(__name__)


def send_push(technicians, title, body, data=None):
    """One notification to every registered phone of these technicians.
    Tokens Expo says are no longer registered (app uninstalled, signed in
    elsewhere) are dropped.
    """
    if not getattr(settings, 'EXPO_PUSH_ENABLED', True):
        return
    tokens = list(
        PushDevice.objects.filter(technician__in=[t for t in technicians if t is not None])
        .values_list('token', flat=True),
    )
    if not tokens:
        return
    messages = [
        {'to': token, 'title': title, 'body': body, 'data': data or {}, 'sound': 'default'} for token in tokens
    ]
    request = Request(
        EXPO_PUSH_URL, data=json.dumps(messages).encode(),
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
    )
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            tickets = json.loads(response.read()).get('data', [])
    except Exception:  # noqa: BLE001 — best-effort, same as email
        logger.warning('Push notification failed', exc_info=True)
        return
    gone = [
        token for token, ticket in zip(tokens, tickets)
        if ticket.get('status') == 'error' and ticket.get('details', {}).get('error') == 'DeviceNotRegistered'
    ]
    if gone:
        PushDevice.objects.filter(token__in=gone).delete()


def push_in_their_language(technician, title_fn, body_fn, data=None):
    """Sends to one technician, worded in their own app language."""
    if technician is None:
        return
    with translation.override(technician.language or 'en'):
        send_push([technician], title_fn(), body_fn(), data)


def push_assigned(technician, task, is_lead):
    push_in_their_language(
        technician,
        lambda: _('New task assigned') if is_lead else _('Added as helper'),
        lambda: _('%(number)s — %(site)s') % {'number': task.task_number, 'site': task.site.name},
        {'type': 'my_task', 'task_id': task.pk},
    )
