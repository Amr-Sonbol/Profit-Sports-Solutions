"""The audit trail: who did what, when, from where — and what it changed.

Every change to the records that matter is recorded here by itself (signals),
whichever page or app screen made it: who (the signed-in person; the app's too,
from its token), their IP address, the record, and each field's value before and
after. Sign-ins (website and app), failed ones and sign-outs too. Django's own
admin log (LogEntry: account actions, the admin site) is copied in, so the Audit
log page reads one trail (people.AuditEntry). Passwords are never kept: only
that one changed.

A change made in bulk (`QuerySet.update()`) isn't seen by signals; those are
the app's own bookkeeping (notifications read, and the like), not people's edits.
"""
import logging
from contextvars import ContextVar

from django.apps import apps
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.utils import timezone
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

_actor = ContextVar('audit_actor', default=None)
_ip = ContextVar('audit_ip', default=None)

# The records whose changes are kept, and fields left out of them (bookkeeping, not decisions).
AUDITED = {
    'customers.Customer': (), 'customers.Site': (), 'customers.Asset': (), 'customers.PortalUser': (),
    'people.Technician': ('setup_link_sent_at',), 'people.RolePermission': (), 'people.CustomRole': (),
    'people.NotificationSettings': (),
    'reference.Country': (), 'reference.Brand': (), 'reference.Part': (), 'reference.TaskType': (),
    'reference.Skill': (), 'reference.ConductArea': (),
    'tasks.Task': ('on_the_way_notified_at', 'reminder_sent_at'), 'tasks.TaskPartNeeded': ('updated_at',),
    'tasks.TaskProduct': (), 'tasks.StockAdjustment': (), 'tasks.SiteReadiness': (),
    'tasks.CustomerTicket': ('token',), 'reports.WorkReport': (),
    'auth.User': ('last_login',),
}
# Never kept, only that they changed.
SECRET_FIELDS = {'password'}


def client_ip(request):
    """The address a request came from: the one our proxy added (the last in
    X-Forwarded-For — the earlier ones are whatever the client sent, so not to be
    trusted), else the connection's own. Never anything that isn't an address."""
    import ipaddress

    forwarded = [part.strip() for part in request.META.get('HTTP_X_FORWARDED_FOR', '').split(',') if part.strip()]
    for candidate in (forwarded[-1] if forwarded else None, request.META.get('REMOTE_ADDR')):
        if not candidate:
            continue
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            continue
    return None


class AuditContextMiddleware:
    """Notes who is making the request and from where, for what it changes."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        actor = _actor.set(user.pk if user is not None and user.is_authenticated else None)
        ip = _ip.set(client_ip(request))
        try:
            return self.get_response(request)
        finally:
            _actor.reset(actor)
            _ip.reset(ip)


def set_actor(user):
    """The app signs in inside the view (its token), after the middleware: it says who here."""
    _actor.set(user.pk if user is not None else None)


def record(action, obj=None, changes=None, message='', user=None, user_id=None, ip=None, at=None):
    """One entry in the audit trail (people.AuditEntry). It never stops the work it
    records: a failure here is logged (Sentry) and the save goes on."""
    try:
        # Its own savepoint: a failed entry is undone alone, never the change it records.
        with transaction.atomic():
            return _record(action, obj, changes, message, user, user_id, ip, at)
    except Exception:
        logger.exception('Audit entry not recorded: %s %s', action, obj.__class__.__name__ if obj is not None else '')
        return None


def _record(action, obj, changes, message, user, user_id, ip, at):
    from django.contrib.auth import get_user_model

    from people.models import AuditEntry
    actor_id = user.pk if user is not None else (user_id if user_id is not None else _actor.get())
    actor_name = ''
    if actor_id:
        actor = user or get_user_model().objects.filter(pk=actor_id).select_related('technician').first()
        if actor is not None:
            technician = getattr(actor, 'technician', None)
            actor_name = technician.full_name if technician else actor.get_username()
        else:
            actor_id = None
    return AuditEntry.objects.create(
        at=at or timezone.now(), actor_id=actor_id, actor_name=actor_name[:150], ip=ip or _ip.get(), action=action,
        object_type=obj._meta.label_lower if obj is not None else '',
        object_id=str(obj.pk) if obj is not None and obj.pk is not None else '',
        object_repr=str(obj)[:200] if obj is not None else '', changes=changes or {}, message=message,
    )


def _never_blocks(handler):
    """A signal handler of the audit trail: whatever goes wrong in it is logged, never
    raised into the save, delete or sign-in it watches."""
    from functools import wraps

    @wraps(handler)
    def wrapper(*args, **kwargs):
        try:
            handler(*args, **kwargs)
        except Exception:
            logger.exception('Audit trail: %s failed', handler.__name__)
    return wrapper


def _snapshot(instance, skipped):
    values = {}
    for field in instance._meta.concrete_fields:
        if field.name in skipped:
            continue
        values[field.name] = '•' if field.name in SECRET_FIELDS else field.value_to_string(instance)
    return values


@_never_blocks
def _before_save(sender, instance, raw=False, **kwargs):
    if raw or instance.pk is None:
        return
    old = sender._default_manager.filter(pk=instance.pk).first()
    instance._audit_before = _snapshot(old, AUDITED[sender._meta.label]) if old is not None else None
    if old is not None:
        # A password: only whether it changed (its stored hash says so; never kept).
        instance._audit_password_changed = getattr(old, 'password', None) != getattr(instance, 'password', None)


@_never_blocks
def _after_save(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    from people.models import AuditEntry
    after = _snapshot(instance, AUDITED[sender._meta.label])
    before = getattr(instance, '_audit_before', None)
    if created or before is None:
        changes = {name: [None, value] for name, value in after.items() if value not in ('', 'None') and name != 'id'}
        record(AuditEntry.Action.CREATED, instance, changes)
        return
    changes = {name: [before.get(name), value] for name, value in after.items() if before.get(name) != value}
    if getattr(instance, '_audit_password_changed', False):
        changes['password'] = ['•', '• (changed)']
    if changes:
        record(AuditEntry.Action.CHANGED, instance, changes)


@_never_blocks
def _after_delete(sender, instance, **kwargs):
    from people.models import AuditEntry
    record(AuditEntry.Action.DELETED, instance,
           {name: [value, None] for name, value in _snapshot(instance, AUDITED[sender._meta.label]).items()
            if value not in ('', 'None')})


@_never_blocks
def _admin_log(sender, instance, created, raw=False, **kwargs):
    """Django's admin log (account actions, the admin site), copied into the trail."""
    if raw or not created:
        return
    from people.models import AuditEntry
    action = {ADDITION: AuditEntry.Action.CREATED, CHANGE: AuditEntry.Action.ACCOUNT,
              DELETION: AuditEntry.Action.DELETED}.get(instance.action_flag, AuditEntry.Action.ACCOUNT)
    entry = record(action, None, None, instance.get_change_message(), user_id=instance.user_id, at=instance.action_time)
    if entry is None:
        return
    entry.object_type = instance.content_type.model_class()._meta.label_lower if instance.content_type else ''
    entry.object_id = instance.object_id or ''
    entry.object_repr = instance.object_repr[:200]
    entry.save(update_fields=['object_type', 'object_id', 'object_repr'])


@_never_blocks
def _signed_in(sender, request, user, **kwargs):
    from people.models import AuditEntry
    record(AuditEntry.Action.SIGNED_IN, user, user=user, ip=client_ip(request) if request else None,
           message='website')


@_never_blocks
def _signed_out(sender, request, user, **kwargs):
    from people.models import AuditEntry
    if user is not None:
        record(AuditEntry.Action.SIGNED_OUT, user, user=user, ip=client_ip(request) if request else None)


@_never_blocks
def _sign_in_failed(sender, credentials, request=None, **kwargs):
    from people.models import AuditEntry
    # Which account was tried (never the password).
    record(AuditEntry.Action.SIGN_IN_FAILED, None, ip=client_ip(request) if request else None,
           message=str(credentials.get('username', ''))[:150])


def connect():
    """Called once the apps are ready (people.apps)."""
    for label in AUDITED:
        model = apps.get_model(label)
        uid = f'audit:{label}'
        pre_save.connect(_before_save, sender=model, dispatch_uid=f'{uid}:pre', weak=False)
        post_save.connect(_after_save, sender=model, dispatch_uid=f'{uid}:post', weak=False)
        post_delete.connect(_after_delete, sender=model, dispatch_uid=f'{uid}:del', weak=False)
    post_save.connect(_admin_log, sender=LogEntry, dispatch_uid='audit:logentry', weak=False)
    user_logged_in.connect(_signed_in, dispatch_uid='audit:in', weak=False)
    user_logged_out.connect(_signed_out, dispatch_uid='audit:out', weak=False)
    user_login_failed.connect(_sign_in_failed, dispatch_uid='audit:failed', weak=False)


def trail(obj):
    """Who created `obj` and who last changed it (AuditEntry or None each), and —
    for a task — what was removed from it (its parts, machine lines, documents):
    the newest 20. For the record's own page (templatetags.record_trail) and the app."""
    from people.models import AuditEntry
    entries = AuditEntry.objects.filter(
        object_type=obj._meta.label_lower, object_id=str(obj.pk),
    ).select_related('actor__technician')
    removed = []
    if obj._meta.label_lower == 'tasks.task':
        removed = list(AuditEntry.objects.filter(
            action=AuditEntry.Action.DELETED, changes__task__0=str(obj.pk),
        ).select_related('actor__technician').order_by('-at')[:20])
    return {
        'created': entries.filter(action=AuditEntry.Action.CREATED).order_by('at').first(),
        'changed': entries.filter(action=AuditEntry.Action.CHANGED).order_by('-at', '-pk').first(),
        'removed': removed,
    }


def who(entry):
    """"Sara Super (Supervisor)" for an audit entry; "System" when nobody did it."""
    if entry is None:
        return ''
    technician = getattr(entry.actor, 'technician', None) if entry.actor_id else None
    name = entry.actor_name or str(_('System'))
    return f'{name} ({technician.title})' if technician else name
