"""Customer portal logins — several people per customer (PortalUser), each
with their own: the secure one-time link a person uses to set their
password, each login's status for the customer's Portal login section and
the Customers list, and the emails that carry the link (English, plus
Arabic for customers in Arab countries).
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

from spots.hosts import portal_url

LINK_VALID_DAYS = 7
LINK_SALT = 'customers.portal-link'
# A customer changing their own sign-in email confirms the new address
# through a link sent there; it works once (it names the current one).
EMAIL_CHANGE_VALID_DAYS = 2
EMAIL_CHANGE_SALT = 'customers.email-change'

# Arab League members (ISO 3166 codes): customers there get each account
# email in English and Arabic; everyone else gets English only.
ARAB_COUNTRY_CODES = {
    'AE', 'BH', 'DJ', 'DZ', 'EG', 'IQ', 'JO', 'KM', 'KW', 'LB', 'LY', 'MA', 'MR', 'OM', 'PS', 'QA', 'SA', 'SD',
    'SO', 'SY', 'TN', 'YE',
}


class AccountStatus(models.TextChoices):
    NO_EMAIL = 'no_email', gettext_lazy('No email')
    NOT_INVITED = 'not_invited', gettext_lazy('Not invited')
    INVITED = 'invited', gettext_lazy('Invited — awaiting setup')
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
    user = get_user_model().objects.filter(pk=data.get('u'), is_active=True).select_related('portal_user').first()
    if user is None or not hasattr(user, 'portal_user') or data.get('f') != _fingerprint(user):
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


def account_status(person, locked, now=None):
    """One person's login: disabled, locked, invited (or its link expired) or active."""
    user = person.user
    if not user.is_active:
        return AccountStatus.DISABLED
    if user.username.lower() in locked:
        return AccountStatus.LOCKED
    if person.must_change_password:
        now = now or timezone.now()
        if person.invited_at and now - person.invited_at > timedelta(days=LINK_VALID_DAYS):
            return AccountStatus.EXPIRED
        return AccountStatus.INVITED
    return AccountStatus.ACTIVE


# What most needs doing first, when a customer's people are in different states.
_URGENCY = [AccountStatus.LOCKED, AccountStatus.EXPIRED, AccountStatus.INVITED, AccountStatus.ACTIVE,
            AccountStatus.DISABLED]


def customer_statuses(customer, locked, now=None):
    """Every status among a customer's people — or, with no one yet, not
    invited (there's a contact email to invite) or no email."""
    people = customer.portal_users.all()
    if not people:
        return {AccountStatus.NOT_INVITED if customer.contact_email else AccountStatus.NO_EMAIL}
    return {account_status(person, locked, now) for person in people}


def customer_status(statuses):
    """The one status a customer's row shows: what most needs doing first."""
    return next((status for status in _URGENCY if status in statuses), next(iter(statuses)))


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


def _send_link_email(request, person, subject_msgid, body_msgid, button_msgid):
    user = person.user
    link = portal_url(request, reverse('customers:portal_account_link', args=[make_link_token(user)]))
    _send_account_email(
        person, user.email, subject_msgid, body_msgid, button_msgid,
        {'link': link, 'username': user.username, 'days': LINK_VALID_DAYS}, link,
    )


def _send_account_email(person, recipient, subject_msgid, body_msgid, button_msgid, values, link=None):
    """One account email to a person, in their customer's languages; `link`,
    when given, shows as a button in the HTML version."""
    from tasks.views import _send_notification_email

    customer = person.customer
    values = {'contact': person.user.first_name or customer.contact_name or customer.name, **values}
    versions = _in_languages(email_languages(customer), lambda: {
        'subject': str(subject_msgid), 'body': str(body_msgid) % values, 'button': str(button_msgid or ''),
    })
    subject = ' | '.join(version['subject'] for _code, version in versions)
    text = '\n\n— — —\n\n'.join(version['body'] for _code, version in versions)
    sections = [{
        'lang': code, 'dir': 'rtl' if code == 'ar' else 'ltr', 'button': version['button'],
        # The HTML version shows the link's own paragraph as a button.
        'paragraphs': version['body'].strip().split('\n\n'),
    } for code, version in versions]
    _send_notification_email(
        subject, text, 'customers/email/portal_link.html', {'sections': sections, 'link': link}, [recipient],
    )


# The texts live here as lazy msgids so makemessages finds them; they're
# translated inside _in_languages, once per language.
WELCOME_SUBJECT = gettext_lazy('Welcome to the Profit Sports Solutions customer portal')
WELCOME_BODY = gettext_lazy(
    'Dear %(contact)s,\n\n'
    'Your customer portal account with Profit Sports Solutions has been created. Through the portal you may '
    'submit service requests, follow their progress and review your service history.\n\n'
    'To activate your account, please set your password and complete your company details using the secure '
    'link below. The link is valid for %(days)s days and can be used once.\n\n'
    '%(link)s\n\n'
    'Your username: %(username)s\n\n'
    'Best regards,\n'
    'Profit Sports Solutions\n'
)
WELCOME_BUTTON = gettext_lazy('Activate my account')
RESET_SUBJECT = gettext_lazy('Set a new password for your Profit Sports Solutions portal account')
RESET_BODY = gettext_lazy(
    'Dear %(contact)s,\n\n'
    'A link to set a new password for your customer portal account has been issued. Please use the secure link '
    'below; it is valid for %(days)s days and can be used once.\n\n'
    '%(link)s\n\n'
    'Your username: %(username)s\n\n'
    'If you did not request this, please disregard this email.\n\n'
    'Best regards,\n'
    'Profit Sports Solutions\n'
)
RESET_BUTTON = gettext_lazy('Set a new password')


def add_person(customer, email, name=''):
    """A new person at the customer: their login (username = email, no
    usable password until they set one through the welcome link). None,
    creating nothing, if that email already belongs to a login."""
    from .models import PortalUser

    email = (email or '').strip()
    User = get_user_model()
    if not email or User.objects.filter(username__iexact=email).exists():
        return None
    user = User(username=email, email=email, first_name=(name or '').strip()[:150])
    user.set_unusable_password()
    user.save()
    return PortalUser.objects.create(customer=customer, user=user, must_change_password=True)


def _prepare_welcome(person):
    """A fresh start for the welcome link: no usable password, to be set at sign-in."""
    person.user.set_unusable_password()
    person.user.save(update_fields=['password'])
    person.must_change_password = True
    person.invited_at = timezone.now()
    person.save(update_fields=['must_change_password', 'invited_at'])


def send_welcome(request, person):
    """Email a person the welcome link. Sending again invalidates any earlier link."""
    _prepare_welcome(person)
    _send_link_email(request, person, WELCOME_SUBJECT, WELCOME_BODY, WELCOME_BUTTON)


def invite_customer(request, customer):
    """A customer no one can sign in to yet: their contact becomes the first
    person, and is sent the welcome email. False, sending nothing, without a
    contact email or when it already belongs to a login."""
    person = add_person(customer, customer.contact_email, customer.contact_name)
    if person is None:
        return False
    send_welcome(request, person)
    return True


def welcome_link(request, person):
    """The same welcome link the email carries, for staff to send another way
    (WhatsApp, say) — when the email doesn't arrive. Any earlier link stops working."""
    _prepare_welcome(person)
    return portal_url(request, reverse('customers:portal_account_link', args=[make_link_token(person.user)]))


def send_password_link(request, person):
    """Email a set-a-new-password link. The current password keeps working
    until the person actually uses the link."""
    _send_link_email(request, person, RESET_SUBJECT, RESET_BODY, RESET_BUTTON)


EMAIL_CHANGE_SUBJECT = gettext_lazy('Confirm your new sign-in email for the Profit Sports Solutions portal')
EMAIL_CHANGE_BODY = gettext_lazy(
    'Dear %(contact)s,\n\n'
    'A request was made to use this address to sign in to your customer portal account. To confirm it, please '
    'use the secure link below; it is valid for %(days)s days and can be used once.\n\n'
    '%(link)s\n\n'
    'Until you confirm, you keep signing in with your current email. If you did not request this, please '
    'disregard this email.\n\n'
    'Best regards,\n'
    'Profit Sports Solutions\n'
)
EMAIL_CHANGE_BUTTON = gettext_lazy('Confirm my new email')
EMAIL_CHANGED_SUBJECT = gettext_lazy('Your Profit Sports Solutions portal sign-in email was changed')
EMAIL_CHANGED_BODY = gettext_lazy(
    'Dear %(contact)s,\n\n'
    'The sign-in email for your customer portal account was changed to %(new_email)s. From now on, please sign '
    'in with that address.\n\n'
    'If you did not make this change, please contact us straight away.\n\n'
    'Best regards,\n'
    'Profit Sports Solutions\n'
)


def make_email_change_token(user, new_email):
    return signing.dumps({'u': user.pk, 'o': user.username, 'e': new_email}, salt=EMAIL_CHANGE_SALT)


def email_change_for_token(token):
    """(the login, its new email) for a confirmation link, or (None, None)
    if it's tampered with, older than EMAIL_CHANGE_VALID_DAYS, already
    used (the login's email is no longer the one it was made for), the
    login is disabled, or the new email has since been taken."""
    try:
        data = signing.loads(token, salt=EMAIL_CHANGE_SALT, max_age=timedelta(days=EMAIL_CHANGE_VALID_DAYS))
    except signing.BadSignature:
        return None, None
    User = get_user_model()
    user = User.objects.filter(pk=data.get('u'), is_active=True).select_related('portal_user').first()
    new_email = data.get('e', '')
    if user is None or not hasattr(user, 'portal_user') or user.username != data.get('o'):
        return None, None
    if User.objects.filter(username__iexact=new_email).exclude(pk=user.pk).exists():
        return None, None
    return user, new_email


def send_email_change_link(request, person, new_email):
    """Email the confirmation link to the new address; nothing changes until
    it's opened."""
    link = portal_url(request, 
        reverse('customers:portal_email_confirm', args=[make_email_change_token(person.user, new_email)]),
    )
    _send_account_email(
        person, new_email, EMAIL_CHANGE_SUBJECT, EMAIL_CHANGE_BODY, EMAIL_CHANGE_BUTTON,
        {'link': link, 'days': EMAIL_CHANGE_VALID_DAYS}, link,
    )


def send_email_changed_notice(person, old_email):
    """Tell the old address its sign-in email was changed, in case it
    wasn't them."""
    _send_account_email(
        person, old_email, EMAIL_CHANGED_SUBJECT, EMAIL_CHANGED_BODY, None, {'new_email': person.user.email},
    )


def log_account_action(request, customer, message, user=None):
    """Record a portal-account action in the Audit log (Django's LogEntry,
    the same table the audit log screen reads) — by the signed-in user, or
    `user` when the customer acted through an emailed link, signed out."""
    from django.contrib.admin.models import CHANGE, LogEntry

    LogEntry.objects.log_actions(
        user_id=(user or request.user).pk, queryset=[customer], action_flag=CHANGE, change_message=message, single_object=True,
    )
