"""Customer portal accounts: the secure one-time link a customer uses to
set their password, each account's status for the Customer accounts
screen, and the emails that carry the link (English, plus Arabic for
customers in Arab countries).
"""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import models
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.crypto import salted_hmac
from django.utils.translation import gettext_lazy

LINK_VALID_DAYS = 7
LINK_SALT = 'customers.portal-link'

# Arab League members (ISO 3166 codes): customers there get each account
# email in English and Arabic; everyone else gets English only.
ARAB_COUNTRY_CODES = {
    'AE', 'BH', 'DJ', 'DZ', 'EG', 'IQ', 'JO', 'KM', 'KW', 'LB', 'LY', 'MA', 'MR', 'OM', 'PS', 'QA', 'SA', 'SD',
    'SO', 'SY', 'TN', 'YE',
}


class AccountStatus(models.TextChoices):
    NO_EMAIL = 'no_email', gettext_lazy('No email')
    NOT_INVITED = 'not_invited', gettext_lazy('Not invited')
    INVITED = 'invited', gettext_lazy('Invited, not set up yet')
    EXPIRED = 'expired', gettext_lazy('Invitation expired')
    ACTIVE = 'active', gettext_lazy('Active')
    LOCKED = 'locked', gettext_lazy('Locked')
    DISABLED = 'disabled', gettext_lazy('Disabled')


def _fingerprint(user):
    # Changes the moment the password does, so each link works only once.
    return salted_hmac(LINK_SALT, f'{user.pk}:{user.password}').hexdigest()[:20]


def make_link_token(user):
    return signing.dumps({'u': user.pk, 'f': _fingerprint(user)}, salt=LINK_SALT)


def user_for_link_token(token):
    """The customer login a link belongs to, or None if it's tampered
    with, older than LINK_VALID_DAYS, already used, or the login is
    disabled."""
    try:
        data = signing.loads(token, salt=LINK_SALT, max_age=timedelta(days=LINK_VALID_DAYS))
    except signing.BadSignature:
        return None
    user = get_user_model().objects.filter(pk=data.get('u'), is_active=True).select_related('customer').first()
    if user is None or not hasattr(user, 'customer') or data.get('f') != _fingerprint(user):
        return None
    return user


def locked_usernames():
    """Logins currently locked out by django-axes, lower-cased, mapped to
    when the lockout happened. Axes locks by account (username; see
    settings.AXES_LOCKOUT_PARAMETERS)."""
    from axes.models import AccessAttempt

    since = timezone.now() - timedelta(hours=settings.AXES_COOLOFF_TIME)
    return {
        username.lower(): attempt_time for username, attempt_time in AccessAttempt.objects.filter(
            failures_since_start__gte=settings.AXES_FAILURE_LIMIT, attempt_time__gte=since,
        ).values_list('username', 'attempt_time') if username
    }


def account_status(customer, locked, now=None):
    user = customer.user
    if user is None:
        return AccountStatus.NOT_INVITED if customer.contact_email else AccountStatus.NO_EMAIL
    if not user.is_active:
        return AccountStatus.DISABLED
    if user.username.lower() in locked:
        return AccountStatus.LOCKED
    if customer.must_change_password:
        now = now or timezone.now()
        if customer.invited_at and now - customer.invited_at > timedelta(days=LINK_VALID_DAYS):
            return AccountStatus.EXPIRED
        return AccountStatus.INVITED
    return AccountStatus.ACTIVE


def email_languages(customer):
    """English for everyone; Arabic too, in the same email, for a customer
    in an Arab country."""
    return ('en', 'ar') if customer.country.iso_code.upper() in ARAB_COUNTRY_CODES else ('en',)


def _in_languages(languages, build):
    """build() run once per language, in order."""
    parts = []
    for code in languages:
        with translation.override(code):
            parts.append((code, build()))
    return parts


def _send_link_email(request, customer, subject_msgid, body_msgid, button_msgid):
    from tasks.views import _send_notification_email

    user = customer.user
    link = request.build_absolute_uri(reverse('customers:portal_account_link', args=[make_link_token(user)]))
    contact = customer.contact_name or customer.name
    values = {'contact': contact, 'link': link, 'username': user.username, 'days': LINK_VALID_DAYS}
    versions = _in_languages(email_languages(customer), lambda: {
        'subject': str(subject_msgid), 'body': str(body_msgid) % values, 'button': str(button_msgid),
    })
    subject = ' | '.join(version['subject'] for _code, version in versions)
    text = '\n\n— — —\n\n'.join(version['body'] for _code, version in versions)
    sections = [{
        'lang': code, 'dir': 'rtl' if code == 'ar' else 'ltr', 'button': version['button'],
        # The HTML version shows the link's own paragraph as a button.
        'paragraphs': version['body'].strip().split('\n\n'),
    } for code, version in versions]
    _send_notification_email(
        subject, text, 'customers/email/portal_link.html', {'sections': sections, 'link': link}, [user.email],
    )


# The texts live here as lazy msgids so makemessages finds them; they're
# translated inside _in_languages, once per language.
WELCOME_SUBJECT = gettext_lazy('Welcome to the Profit Sports Solutions customer portal')
WELCOME_BODY = gettext_lazy(
    'Dear %(contact)s,\n\n'
    'Your Profit Sports Solutions customer portal account is ready. In the portal you can send service '
    'requests, follow their progress and see your service history.\n\n'
    'To get started, set your password and complete your company details using the link below. It works once '
    'and expires in %(days)s days.\n\n'
    '%(link)s\n\n'
    'Your username: %(username)s\n\n'
    'Best regards,\n'
    'Profit Sports Solutions\n'
)
WELCOME_BUTTON = gettext_lazy('Activate my account')
RESET_SUBJECT = gettext_lazy('Set a new password for your Profit Sports Solutions portal account')
RESET_BODY = gettext_lazy(
    'Dear %(contact)s,\n\n'
    'Use the link below to set a new password for your customer portal account. It works once and expires in '
    '%(days)s days.\n\n'
    '%(link)s\n\n'
    'Your username: %(username)s\n\n'
    "If you didn't ask for this, you can ignore this email.\n\n"
    'Best regards,\n'
    'Profit Sports Solutions\n'
)
RESET_BUTTON = gettext_lazy('Set a new password')


def send_welcome(request, customer, email=None):
    """Create the login if there isn't one yet (username = email, no usable
    password until the customer sets one) and email the welcome link.
    Sending again invalidates any earlier link. Returns False, sending
    nothing, if that email already belongs to a different login.
    """
    User = get_user_model()
    if customer.user_id is None:
        email = (email or customer.contact_email).strip()
        if User.objects.filter(username__iexact=email).exists():
            return False
        user = User(username=email, email=email)
        user.set_unusable_password()
        user.save()
        customer.user = user
    else:
        customer.user.set_unusable_password()
        customer.user.save(update_fields=['password'])
    customer.must_change_password = True
    customer.invited_at = timezone.now()
    customer.save(update_fields=['user', 'must_change_password', 'invited_at'])
    _send_link_email(request, customer, WELCOME_SUBJECT, WELCOME_BODY, WELCOME_BUTTON)
    return True


def send_password_link(request, customer):
    """Email a set-a-new-password link. The current password keeps working
    until the customer actually uses the link."""
    _send_link_email(request, customer, RESET_SUBJECT, RESET_BODY, RESET_BUTTON)


def log_account_action(request, customer, message):
    """Record a portal-account action in the Audit log (Django's LogEntry,
    the same table the audit log screen reads)."""
    from django.contrib.admin.models import CHANGE, LogEntry

    LogEntry.objects.log_actions(
        user_id=request.user.pk, queryset=[customer], action_flag=CHANGE, change_message=message, single_object=True,
    )
