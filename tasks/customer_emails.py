"""Every email to a customer, one standard (docs/go_live_plan.md: step 5):

- the subject: what happened, and their reference — their request's number
  (UAE-T0123), or a visit's own (UAE-0015) only when it came from no request;
- "Dear …", one clear sentence of what happened, the details, what happens
  next; a button to their request, where they reply or add photos;
- English, and Arabic too in the same email for a customer in an Arab country
  (customers.accounts.email_languages);
- a preview line for the inbox; the emails of one request grouped in mail
  programs that group by headers (References);
- signed by Customer Service (tasks/email/_signature.html), replies to its
  mailbox (tasks.views._send_notification_email).

The texts are lazy msgids, translated once per language inside send().
"""
from django.conf import settings
from django.urls import reverse
from django.utils import formats, timezone, translation
from django.utils.translation import gettext_lazy as _

TEMPLATE = 'tasks/email/customer.html'

GREETING = _('Dear %(contact)s,')
CLOSING = _('Kind regards,')
FOLLOW_UP = _('You can follow your request here:')
VIEW_REQUEST = _('View your request')
FOOTER = _('To reply, please use your request in our customer portal, so our team can see it.')
REQUEST_REFERENCE = _('Request %(number)s')
VISIT_REFERENCE = _('Visit %(number)s')
SUBJECT = _('%(title)s — %(reference)s')


def languages(country):
    """English for everyone; Arabic too, in the same email, in an Arab country."""
    from customers.accounts import ARAB_COUNTRY_CODES
    return ('en', 'ar') if country.iso_code.upper() in ARAB_COUNTRY_CODES else ('en',)


def request_of(task):
    """The customer's request a task came from (its own, or one of its machines'), or None."""
    from tasks.models import CustomerTicket
    ticket = getattr(task, 'ticket', None) or next(
        (machine.ticket for machine in task.ticket_machines.select_related('ticket')[:1]), None,
    )
    return ticket if ticket is not None and ticket.kind == CustomerTicket.Kind.CUSTOMER else None


def request_link(ticket, request=None):
    """Their request on the portal, or None (no request; no address to build a link with)."""
    if ticket is None or not (settings.PORTAL_HOST or request is not None):
        return None
    from spots.hosts import portal_url
    return portal_url(request, reverse('tasks:ticket_status', args=[ticket.token]))


def local(moment, country):
    """A moment in the country's own time, for the date and time lines."""
    from zoneinfo import ZoneInfo
    return timezone.localtime(moment, ZoneInfo(country.timezone))


def date_text(moment, country):
    return formats.date_format(local(moment, country), 'l j F Y')


def time_text(moment, country):
    return formats.time_format(local(moment, country), 'g:i A')


def day_text(day):
    """A date with no time of day (an expected arrival)."""
    return formats.date_format(day, 'l j F Y')


def send(*, country, recipient, contact, title, paragraphs, values=None, ticket=None, task=None,
         request=None, link=None, button=None, attachment=None, background=True):
    """One customer email. `title` (lazy) heads the subject; `paragraphs` are
    lazy texts (or callables returning one, for what's built per language —
    dates, optional lines) filled with `values`, whose callables are run per
    language too. Their request's link (or `link`, with its `button`) shows
    as a button. False when there's no address to send to."""
    from tasks.views import _send_notification_email

    if not recipient:
        return False
    ticket = ticket or (request_of(task) if task is not None else None)
    number, reference_text = (
        (ticket.ticket_number, REQUEST_REFERENCE) if ticket is not None else (task.task_number, VISIT_REFERENCE)
    )
    if link is None:
        link, button = request_link(ticket, request), VIEW_REQUEST

    sections = []
    for code in languages(country):
        with translation.override(code):
            filled = {
                key: str(value() if callable(value) else value) for key, value in (values or {}).items()
            }
            filled['contact'] = contact
            reference = str(reference_text) % {'number': number}
            body = [str(GREETING) % filled]
            for paragraph in paragraphs:
                text = paragraph() if callable(paragraph) else paragraph
                if text:
                    body.append(str(text) % {**filled, 'reference': reference})
            if link:
                body += [str(FOLLOW_UP) if button == VIEW_REQUEST else '', link]
            body.append(str(CLOSING))
            sections.append({
                'lang': code, 'dir': 'rtl' if code == 'ar' else 'ltr', 'button': str(button or ''),
                'footer': str(FOOTER),
                'subject': str(SUBJECT) % {'title': str(title), 'reference': reference},
                'paragraphs': [text for text in body if text],
            })
    subject = ' | '.join(section['subject'] for section in sections)
    text = '\n\n— — —\n\n'.join('\n\n'.join(section['paragraphs']) for section in sections)
    return _send_notification_email(
        subject, text, TEMPLATE,
        # The inbox's preview: what happened, in the first language.
        {'sections': sections, 'link': link, 'preview': sections[0]['paragraphs'][1],
         'footer_lines': [section['footer'] for section in sections]},
        [recipient], attachment=attachment, background=background,
        # One request's emails grouped, where the mail program groups by headers.
        headers={'References': f'<request-{number}@profitsports.solutions>'},
    )


# ---- The emails' texts (each: the subject's title, then its paragraphs) ----

RECEIVED_TITLE = _('Request received')
RECEIVED_BODY = _('Thank you for contacting us. We have received your request %(reference)s for %(site)s.')
RECEIVED_NEXT = _(
    'Our team responds within 24 hours, Sunday to Friday, and you will receive an email as soon as there is an '
    'update. If you have not done so already, please add photos or videos of each issue: they help us resolve '
    'it promptly.'
)

REPLY_TITLE = _('New reply from our team')
REPLY_BODY = _('Our team has replied to your request %(reference)s:')
REPLY_BODY_NAMED = _('%(name)s from our team has replied to your request %(reference)s:')

RESOLVED_TITLE = _('Request resolved')
RESOLVED_BODY = _('Your request %(reference)s has been resolved:')
RESOLVED_NEXT = _('If you need anything further, you are welcome to send us a new request from the portal.')

SCHEDULED_TITLE = _('Visit scheduled')
SCHEDULED_BODY = _('Our technician will visit %(site)s on %(date)s at %(time)s.')
SCHEDULED_NEXT = _('If this time is inconvenient, please let us know and we will arrange another.')

ON_THE_WAY_TITLE = _('Our technician is on the way')
ON_THE_WAY_BODY = _('%(technician)s is on the way to %(site)s.')
ON_THE_WAY_TIME = _('Expected around %(time)s.')
OUR_TECHNICIAN = _('Our technician')

DELAY_TITLE = _('Your visit is running late')
DELAY_BODY = _('Our technician for %(site)s is running behind schedule today: %(reason)s')
DELAY_NEXT = _('We apologise for the inconvenience and will arrive as soon as possible.')

SHIPPED_TITLE = _('Your parts have been shipped')
SHIPPED_BODY = _('The parts for %(site)s have been shipped.')
SHIPPED_COURIER = _('Courier: %(courier)s')
SHIPPED_TRACKING = _('Tracking number: %(tracking)s')
SHIPPED_TRACK_LINK = _('Track your shipment: %(tracking_url)s')

REPORT_TITLE = _('Visit report')
REPORT_BODY = _('Please find attached the report of our visit to %(site)s on %(date)s.')
REPORT_NEXT = _('If you have any questions about the work carried out, please let us know.')
REPORT_RATE = _('How was our visit? We would greatly appreciate a moment of your time to tell us.')

PARTS_ORDERED_TITLE = _('Parts ordered for your repair')
PARTS_ORDERED_BODY = _('To complete the repair at %(site)s, we have ordered the parts it needs.')
PARTS_ORDERED_BY = _('We expect them by %(date)s.')
PARTS_ORDERED_VISIT = _('We will arrange the visit as soon as they arrive.')
PARTS_ORDERED_SHIP = _('We will ship them to you as soon as they arrive.')

VISIT_CANCELLED_TITLE = _('Your visit has been cancelled')
VISIT_CANCELLED_BODY = _('The visit planned at %(site)s for your request %(reference)s has been cancelled.')
VISIT_CANCELLED_NEXT = _('Our team is arranging the next step and will contact you shortly.')

REMINDER_TITLE = _('Reminder: your visit tomorrow')
REMINDER_BODY = _('A reminder that our technician will visit %(site)s tomorrow, %(date)s, at %(time)s.')
REMINDER_NEXT = _('If this time is no longer convenient, please let us know as soon as possible.')

FEEDBACK_TITLE = _('How was our visit?')
FEEDBACK_BODY = _(
    'We visited %(site)s on %(date)s, and would greatly appreciate a moment of your time to tell us how it went.'
)
FEEDBACK_NEXT = _('Your feedback helps us keep the standard of service you expect from us.')
FEEDBACK_BUTTON = _('Rate our visit')


def raw(key):
    """A paragraph that is the customer's or our team's own words, as written (never translated)."""
    return lambda: f'%({key})s'
