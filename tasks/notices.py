"""Emails and notices to customers and staff, and how they are sent: every one
through _send_notification_email (the layout, the Customer Service signature
for customers, attachments, in the background with failures recorded); the
customer's (tasks.customer_emails) and the staff's own.
"""
from datetime import timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo
from email.mime.image import MIMEImage
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.finders import find as find_static_file
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import (
    Q,
)
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.crypto import salted_hmac
from django.utils.translation import gettext as _
from customers import notify as notify_customer
from people.models import (
    NotificationSettings, RolePermission, Technician,
)
from spots.email_failures import current_user_id, record_failed_email
from spots.hosts import portal_url, staff_url
from people.push import push_in_their_language
from .models import (
    ShippingCompany, Task, TicketMachine,
)
from .equipment import (  # noqa: E402,F401
    BeyondStock, ChecklistFormSet, DISPATCH_PHOTO_MISSING, EQUIPMENT_STEPS, FOLLOW_UP_STATUSES, NEXT_STEPS, SITE_NOT_READY, STILL_TO_INSTALL, _save_checklist, check_beyond_stock, checklist_formset_rows, checklist_initial, create_follow_up, create_next_step, dispatch_photo_missing, equipment_step, has_checklist, is_installation, lines_to_carry, loading_shortfall, make_checklist_formset, next_step_sites, next_step_supervisor, next_step_supervisors, open_equipment_follow_up, register_installed_machines, save_checklist, save_planned_lines, save_site_readiness, site_is_ready, site_readiness_for_app, still_to_install, warehouse_stock_for_planning,
)


def _in_background(work):
    """Run `work` once the request's changes are saved, on the shared bounded pool
    (spots.background) — the person doesn't wait for the mail server or a PDF."""
    from spots.background import in_background
    return in_background(work)


# What one email can carry in files: mail servers commonly refuse much more.
MAX_EMAIL_ATTACHMENT_BYTES = 20 * 1024 * 1024


# The emails a customer receives (the rest go to our staff): signed by Customer Service,
# their replies to the team's mailbox (CUSTOMER_SERVICE_EMAIL).
CUSTOMER_EMAIL_TEMPLATES = {'tasks/email/customer.html'}  # tasks.customer_emails, customers.accounts
CUSTOMER_SIGNATURE_TEXT = (
    'Profit Sports Solutions | Customer Service\n'
    'Phone: %(phone)s\n'
    'Email: %(email)s\n'
    'https://profitsports.solutions/\n'
    '11th floor, office#1108, Grosvenor Business Tower, Barsha Heights – Dubai, UAE'
)


def _send_notification_email(subject, text_body, template_name, context, recipient_list, attachment=None,
                             background=True, headers=None):
    """Every outbound notification email in this app goes through here
    — a plain-text body for clients that don't render HTML, and a
    matching styled HTML alternative (tasks/templates/tasks/email/)
    for everyone else. A failed send never blocks the action that
    triggered it, but it's logged as an error, so it reaches Sentry
    instead of vanishing.

    attachment, when given, is a real file on the ticket/task itself
    (e.g. a quotation) — attached as-is, not just linked — or a
    (filename, bytes, mimetype) tuple for a generated one (the visit
    report PDF), or a list of files (a message's). Files past
    MAX_EMAIL_ATTACHMENT_BYTES in all are left out — the email links to
    where they are anyway.

    The email is built here (files read now), then sent in the background
    (_in_background) unless `background` is False — for work that already
    runs there. Returns whether it went out, or True once it's handed over.

    The logo is embedded inline (a cid: reference, not a plain URL) —
    this renders outside any request, and an email client fetches remote
    images itself, from wherever it's reading the mail, which may have
    no route at all to a local dev server. Embedding the actual bytes in
    the message is the only way it reliably shows up everywhere.
    """
    # To a customer: signed by Customer Service, its replies to the team's mailbox.
    to_customer = template_name in CUSTOMER_EMAIL_TEMPLATES
    html_body = render_to_string(template_name, {
        **context, 'logo_url': 'cid:logo', 'customer_email': to_customer,
        'service_email': settings.CUSTOMER_SERVICE_EMAIL, 'service_phone': settings.CUSTOMER_SERVICE_PHONE,
    })
    if to_customer:
        signature = CUSTOMER_SIGNATURE_TEXT % {'phone': settings.CUSTOMER_SERVICE_PHONE, 'email': settings.CUSTOMER_SERVICE_EMAIL}
        footer = '\n'.join(context.get('footer_lines') or [])
        text_body = f'{text_body.rstrip()}\n\n--\n{signature}\n\n{footer}'.rstrip()
    email = EmailMultiAlternatives(
        subject=subject, body=text_body, from_email=settings.CUSTOMER_FROM_EMAIL if to_customer else None,
        to=recipient_list, reply_to=[settings.CUSTOMER_SERVICE_EMAIL] if to_customer else None, headers=headers,
    )
    email.attach_alternative(html_body, 'text/html')
    email.mixed_subtype = 'related'
    logo_path = find_static_file('images/email_header.png')
    if logo_path:
        with open(logo_path, 'rb') as logo_file:
            logo_image = MIMEImage(logo_file.read())
        logo_image.add_header('Content-ID', '<logo>')
        logo_image.add_header('Content-Disposition', 'inline', filename='email_header.png')
        email.attach(logo_image)
    if isinstance(attachment, tuple):
        email.attach(*attachment)
    elif attachment:
        total = 0
        for file in attachment if isinstance(attachment, list) else [attachment]:
            if total + file.size > MAX_EMAIL_ATTACHMENT_BYTES:
                continue
            total += file.size
            file.open('rb')
            try:
                email.attach(file.name.rsplit('/', 1)[-1], file.read())
            finally:
                file.close()
    # Who caused it, read now: the background thread doesn't carry the request with it.
    triggered_by_id = current_user_id()

    def deliver():
        try:
            email.send()
        except Exception as error:  # noqa: BLE001 — never block the action; but log it, so it reaches Sentry
            logger.exception('Notification email failed: %s', subject)
            if background:
                # Nobody is waiting for this one: told on their next page instead (spots.email_failures).
                record_failed_email(triggered_by_id, recipient_list, subject, error)
            return False
        return True

    if background:
        sent = _in_background(deliver)
        return True if sent is None else sent
    return deliver()


def send_visit_report_copy(report, email_address, rating_link=None):
    """Email the customer the visit report PDF (reports.pdf), and record
    where and when on the report. The PDF is in the report's language
    (reports.pdf.report_language — Arabic in Saudi Arabia and Egypt); its
    email, like every customer email, in English and Arabic in an Arab
    country (tasks.customer_emails). Best-effort: a failure is logged, never
    raised, and the PDF stays downloadable from the task page.
    """
    from reports.pdf import render_visit_report_pdf, report_language
    from tasks import customer_emails as emails

    task = report.task
    customer = task.site.customer
    try:
        pdf = render_visit_report_pdf(report, report_language(customer))
    except Exception:  # noqa: BLE001 — same as a failed send: log it, don't break the report
        logger.exception('Visit report PDF failed: %s', task.task_number)
        return False
    # Already in the background (see save_work_report): send it from here.
    sent = emails.send(
        country=customer.country, recipient=email_address,
        contact=task.site.contact_name or customer.contact_name or customer.name,
        title=emails.REPORT_TITLE,
        # The rating asked in the same email ("Rate our visit"), not a separate one.
        paragraphs=[emails.REPORT_BODY, emails.REPORT_NEXT, emails.REPORT_RATE if rating_link else None],
        values={'site': task.site.name, 'date': lambda: emails.date_text(report.submitted_at, customer.country)},
        task=task, link=rating_link, button=emails.FEEDBACK_BUTTON if rating_link else None,
        attachment=(f'{task.task_number}-visit-report.pdf', pdf, 'application/pdf'), background=False,
    )
    if sent:
        report.copy_sent_to = email_address
        report.copy_sent_at = timezone.now()
        report.save(update_fields=['copy_sent_to', 'copy_sent_at'])
    return sent


def _send_schedule_notification(task):
    """The visit's date and time to the branch contact (tasks.customer_emails).
    Best-effort: a failed send never blocks the supervisor. Manual every time;
    nothing calls this on its own."""
    from tasks import customer_emails as emails

    country = task.site.customer.country
    emails.send(
        country=country, recipient=task.site.effective_contact_email,
        contact=task.site.effective_contact_name or task.site.customer.name,
        title=emails.SCHEDULED_TITLE, paragraphs=[emails.SCHEDULED_BODY, emails.SCHEDULED_NEXT],
        values={
            'site': task.site.name,
            'date': lambda: emails.date_text(task.scheduled_for, country),
            'time': lambda: emails.time_text(task.scheduled_for, country),
        },
        task=task,
    )


# From this hour (the country's own time) the day before a visit, its reminder goes out.
REMINDER_FROM_HOUR = 9
# Only for a visit the customer was told of at least this long before it.
REMINDER_NOTICE = timedelta(days=7)


def send_visit_reminders(now=None):
    """The day before a visit, from 9 AM in its country: a reminder to the
    branch contact — once, and only when they were told its time at least a
    week before (told a new time, reminded again). Never our own warehouses.
    Run every hour (manage.py send_visit_reminders: the scheduled job,
    docs/go_live_plan.md). How many went."""
    from tasks import customer_emails as emails

    now = now or timezone.now()
    sent = 0
    tasks = Task.objects.filter(
        status__in=[Task.Status.NEW, Task.Status.ASSIGNED, Task.Status.ACCEPTED],
        scheduled_for__gt=now, scheduled_for__lte=now + timedelta(days=2),
        schedule_notified_at__isnull=False, site__customer__is_internal=False,
    ).select_related('site__customer__country')
    for task in tasks:
        country = task.site.customer.country
        local_now = emails.local(now, country)
        if local_now.hour < REMINDER_FROM_HOUR:
            continue
        if emails.local(task.scheduled_for, country).date() != local_now.date() + timedelta(days=1):
            continue
        if task.scheduled_for - task.schedule_notified_at < REMINDER_NOTICE:
            continue
        if task.reminder_sent_at and task.reminder_sent_at >= task.schedule_notified_at:
            continue
        # Moved since they were told, or a change of time waiting for a manager: not a time they know.
        if not customer_has_current_time(task) or schedule_change_pending(task):
            continue
        if not task.site.effective_contact_email:
            continue
        task.reminder_sent_at = now
        task.save(update_fields=['reminder_sent_at'])
        emails.send(
            country=country, recipient=task.site.effective_contact_email,
            contact=task.site.effective_contact_name or task.site.customer.name,
            title=emails.REMINDER_TITLE, paragraphs=[emails.REMINDER_BODY, emails.REMINDER_NEXT],
            values={
                'site': task.site.name,
                'date': lambda task=task, country=country: emails.date_text(task.scheduled_for, country),
                'time': lambda task=task, country=country: emails.time_text(task.scheduled_for, country),
            },
            task=task, background=False,
        )
        sent += 1
    return sent


def tell_customer_on_the_way(task, user):
    """The technician tapped "On the way": the customer hears it — their
    portal bell, and an email to the branch contact (unless they switched it
    off in the portal) with who is coming and the expected time. Once a visit
    day: a second tap or an undo doesn't send it again. Never our own
    warehouses. Best-effort; a failed email never stops the tap."""
    from tasks import customer_emails as emails

    customer = task.site.customer
    if customer.is_internal:
        return
    country_tz = ZoneInfo(customer.country.timezone)
    today = timezone.localtime(timezone.now(), country_tz).date()
    if task.on_the_way_notified_at and timezone.localtime(task.on_the_way_notified_at, country_tz).date() == today:
        return
    task.on_the_way_notified_at = timezone.now()
    task.save(update_fields=['on_the_way_notified_at'])
    notify_customer.on_the_way(task)
    if not customer.notify_on_the_way:
        return
    technician = getattr(user, 'technician', None)
    # The expected time only when the visit is planned for today.
    today_time = task.scheduled_for and timezone.localtime(task.scheduled_for, country_tz).date() == today
    emails.send(
        country=customer.country, recipient=task.site.effective_contact_email,
        contact=task.site.effective_contact_name or customer.name,
        title=emails.ON_THE_WAY_TITLE,
        paragraphs=[emails.ON_THE_WAY_BODY, emails.ON_THE_WAY_TIME if today_time else None],
        values={
            'site': task.site.name,
            'technician': technician.full_name if technician else emails.OUR_TECHNICIAN,
            'time': lambda: emails.time_text(task.scheduled_for, customer.country) if today_time else '',
        },
        task=task,
    )


def _send_delay_notice(task, reason):
    """A different message from _send_schedule_notification: that one
    confirms a plan, this one apologizes for one slipping — traffic, a
    previous job running long, and so on. Always manual and always
    needs a reason, since there's no automatic way to know the team is
    running behind.
    """
    from tasks import customer_emails as emails

    emails.send(
        country=task.site.customer.country, recipient=task.site.effective_contact_email,
        contact=task.site.effective_contact_name or task.site.customer.name,
        title=emails.DELAY_TITLE, paragraphs=[emails.DELAY_BODY, emails.DELAY_NEXT],
        values={'site': task.site.name, 'reason': reason}, task=task,
    )


# Public tracking-lookup URLs, one per carrier that has a stable one —
# {tracking} is the only placeholder, filled in URL-encoded. Local
# courier and Other have no universal page, so they're left out on
# purpose; the email still names the carrier either way, just without
# a link.
CARRIER_TRACKING_URLS = {
    ShippingCompany.DHL: 'https://www.dhl.com/en/express/tracking.html?AWB={tracking}',
    ShippingCompany.FEDEX: 'https://www.fedex.com/fedextrack/?trknbr={tracking}',
    ShippingCompany.UPS: 'https://www.ups.com/track?tracknum={tracking}',
    ShippingCompany.ARAMEX: 'https://www.aramex.com/track/results?ShipmentNumber={tracking}',
    ShippingCompany.TNT: 'https://www.tnt.com/express/en_us/site/shipping-tools/tracking.html?cons={tracking}',
}


def _send_shipping_notice(country, contact_name, entity_name, company, tracking_number, contact_email,
                          task=None, ticket=None):
    """Shared by a part shipment (a task) and a request's own shipment (a
    ticket). Only the courier and tracking number go to the customer;
    pak_reference_number is internal and never sent. `company` is the raw
    ShippingCompany code (or ''), not its display label — needed as-is to
    look up a tracking URL.
    """
    from tasks import customer_emails as emails

    tracking_url = None
    if company and tracking_number:  # a local courier often has none
        url_template = CARRIER_TRACKING_URLS.get(company)
        if url_template:
            tracking_url = url_template.format(tracking=quote(tracking_number))

    def tracking_lines():
        lines = [str(emails.SHIPPED_COURIER)] if company else []
        if tracking_number:
            lines.append(str(emails.SHIPPED_TRACKING))
        if tracking_url:
            lines.append(str(emails.SHIPPED_TRACK_LINK))
        return '\n'.join(lines)

    emails.send(
        country=country, recipient=contact_email, contact=contact_name,
        title=emails.SHIPPED_TITLE, paragraphs=[emails.SHIPPED_BODY, tracking_lines],
        values={
            'site': entity_name, 'courier': lambda: ShippingCompany(company).label if company else '',
            'tracking': tracking_number, 'tracking_url': tracking_url or '',
        },
        task=task, ticket=ticket,
    )


def _send_shipment_notice_to_supervisor(request, task):
    """Most shipments arrive against a factory tracking number, not a
    customer request — the responsible_supervisor is the one staffing
    the task and the one who actually needs to know parts are on the
    way, so this fires whenever shipping_tracking_number changes,
    separately from _send_shipping_notice above (which is the
    customer-facing one, manual, and about a different audience
    entirely). Best-effort, and only when there's a responsible
    supervisor with a login and an email on file — same guard
    _send_reply_to_staff uses for the same reason.
    """
    supervisor = task.responsible_supervisor
    if not supervisor or not supervisor.user_id or not supervisor.user.email:
        return
    link = staff_url(request, reverse('tasks:task_detail', args=[task.pk]))
    carrier_label = task.get_shipping_company_display() if task.shipping_company else ''
    tracking_url = None
    if task.shipping_company:
        url_template = CARRIER_TRACKING_URLS.get(task.shipping_company)
        if url_template:
            tracking_url = url_template.format(tracking=quote(task.shipping_tracking_number))
    with translation.override('en'):
        subject = _('Your shipment is on its way — %(task_number)s') % {'task_number': task.task_number}
        if task.shipping_company:
            tracking_line = _('Carrier: %(company)s — tracking number %(tracking)s') % {
                'company': carrier_label, 'tracking': task.shipping_tracking_number,
            }
        else:
            tracking_line = _('Tracking number: %(tracking)s') % {'tracking': task.shipping_tracking_number}
        message = _(
            'A tracking number has been assigned to the shipment for %(task_number)s (%(site)s).\n\n'
            '%(tracking_line)s\n\n'
            'View the task:\n'
            '%(link)s\n',
        ) % {
            'task_number': task.task_number, 'site': task.site.name,
            'tracking_line': tracking_line, 'link': link,
        }
    _send_notification_email(
        subject, message, 'tasks/email/shipment_notice_supervisor.html',
        {
            'task_number': task.task_number, 'site': task.site.name, 'carrier_label': carrier_label,
            'tracking_number': task.shipping_tracking_number, 'tracking_url': tracking_url, 'link': link,
        },
        [supervisor.user.email],
    )


def _send_feedback_email(request, feedback):
    """The rating link to the branch contact (tasks.customer_emails).
    Best-effort — a failed send shouldn't stop the supervisor's flow."""
    from tasks import customer_emails as emails

    task = feedback.task
    country = task.site.customer.country
    emails.send(
        country=country, recipient=task.site.effective_contact_email,
        contact=task.site.effective_contact_name or task.site.customer.name,
        title=emails.FEEDBACK_TITLE, paragraphs=[emails.FEEDBACK_BODY, emails.FEEDBACK_NEXT],
        values={'site': task.site.name, 'date': lambda: emails.date_text(task.report.submitted_at, country)},
        task=task, link=portal_url(request, reverse('reports:feedback_form', args=[feedback.token])),
        button=emails.FEEDBACK_BUTTON,
    )


def send_ticket_confirmation_to_customer(request, ticket):
    """The one email a customer gets right when they submit a request —
    best-effort (tasks.customer_emails). Public (no leading underscore):
    customers.views.portal_ticket_new, a different app, calls this
    directly, the same way it already calls save_new_ticket.
    """
    from tasks import customer_emails as emails

    if not NotificationSettings.load().ticket_received_email:  # the admin's Notifications page
        return
    emails.send(
        country=ticket.country, recipient=ticket.contact_email, contact=ticket.contact_name,
        title=emails.RECEIVED_TITLE, paragraphs=[emails.RECEIVED_BODY, emails.RECEIVED_NEXT],
        values={'site': ticket.site_description}, ticket=ticket, request=request,
    )


# Staff setup links: the same kind of signed, one-time link as the
# customer portal's (customers.accounts), with their own salt so a link
# made for one can never open the other.
STAFF_LINK_SALT = 'tasks.staff-setup-link'
STAFF_LINK_VALID_DAYS = 7


def _staff_link_fingerprint(user):
    # Changes the moment the password does, so each link works only once.
    return salted_hmac(STAFF_LINK_SALT, f'{user.pk}:{user.password}').hexdigest()[:20]


def make_staff_link_token(user):
    return signing.dumps({'u': user.pk, 'f': _staff_link_fingerprint(user)}, salt=STAFF_LINK_SALT)


def staff_user_for_link_token(token):
    """The staff login a setup link belongs to, or None if it's tampered
    with, older than STAFF_LINK_VALID_DAYS, already used, or the login or
    the person is no longer active."""
    try:
        data = signing.loads(token, salt=STAFF_LINK_SALT, max_age=timedelta(days=STAFF_LINK_VALID_DAYS))
    except signing.BadSignature:
        return None
    user = get_user_model().objects.filter(
        pk=data.get('u'), is_active=True, technician__is_active=True,
    ).select_related('technician').first()
    if user is None or data.get('f') != _staff_link_fingerprint(user):
        return None
    return user


def send_staff_setup_link(request, technician):
    """Emails a one-time link to set a password: for a new login, or for
    anyone who can't sign in. No password ever goes by email. False if
    there's no email address to send it to.
    """
    user = technician.user
    if not user.email:
        return False
    link = staff_url(request, reverse('tasks:staff_setup', args=[make_staff_link_token(user)]))
    context = {'contact': technician.full_name, 'username': user.get_username(), 'link': link,
               'days': STAFF_LINK_VALID_DAYS}
    with translation.override('en'):
        subject = _('Set up your Profit Sports Solutions account')
        message = _(
            'Dear %(contact)s,\n\n'
            'Your Profit Sports Solutions account has been created. Please set your password using the secure '
            'link below. The link is valid for %(days)s days and can be used once.\n\n'
            '%(link)s\n\n'
            'Your username: %(username)s\n\n'
            'If the link has expired, please ask your manager to send a new one.\n\n'
            'Best regards,\n'
            'Profit Sports Solutions\n'
        ) % context
    _send_notification_email(subject, message, 'tasks/email/staff_setup_link.html', context, [user.email])
    technician.setup_link_sent_at = timezone.now()
    technician.save(update_fields=['setup_link_sent_at'])
    return True


def _send_reply_to_customer(request, reply):
    """Best-effort, and only when the customer gave an email. Any file
    attached to the reply (quotation, report, photo, whatever staff chose
    to include) rides along on the email itself, not just linked from the
    portal. Who wrote it, by first name ("Sara from our team")."""
    from tasks import customer_emails as emails

    if not NotificationSettings.load().reply_to_customer_email:  # the admin's Notifications page
        return
    ticket = reply.ticket
    name = reply.sent_by.first_name if reply.sent_by_id else ''
    emails.send(
        country=ticket.country, recipient=ticket.contact_email, contact=ticket.contact_name,
        title=emails.REPLY_TITLE,
        paragraphs=[emails.REPLY_BODY_NAMED if name else emails.REPLY_BODY, emails.raw('message')],
        values={'name': name, 'message': reply.message}, ticket=ticket, request=request,
        attachment=reply.all_files,
    )


def _send_request_closed(request, ticket):
    """Tell the customer their request was resolved, with our answer (the
    close reason) — best-effort, under the same Notifications switch as
    our replies, and only when they gave an email."""
    from tasks import customer_emails as emails

    if not NotificationSettings.load().reply_to_customer_email:  # the admin's Notifications page
        return
    emails.send(
        country=ticket.country, recipient=ticket.contact_email, contact=ticket.contact_name,
        title=emails.RESOLVED_TITLE, paragraphs=[emails.RESOLVED_BODY, emails.raw('answer'), emails.RESOLVED_NEXT],
        values={'answer': ticket.close_reason}, ticket=ticket, request=request,
    )


def _send_reply_to_staff(request, reply):
    """Best-effort, and only when the ticket is assigned to someone with
    a login and an email on file — there's no fixed office address to
    fall back to.
    """
    if not NotificationSettings.load().reply_to_staff_email:  # the admin's Notifications page
        return
    ticket = reply.ticket
    assignee = ticket.assigned_to
    if not assignee or not assignee.user_id or not assignee.user.email:
        return
    link = staff_url(request, reverse('tasks:ticket_review', args=[ticket.pk]))
    with translation.override('en'):
        subject = _('New customer reply — ticket %(number)s') % {'number': ticket.ticket_number}
        message = _(
            'The customer has replied to ticket %(number)s (%(company)s — %(site)s):\n\n'
            '%(reply_message)s\n\n'
            'View and reply:\n'
            '%(link)s\n',
        ) % {
            'number': ticket.ticket_number, 'company': ticket.company_name, 'site': ticket.site_description,
            'reply_message': reply.message, 'link': link,
        }
    _send_notification_email(
        subject, message, 'tasks/email/reply_to_staff.html',
        {
            'ticket_number': ticket.ticket_number, 'company': ticket.company_name,
            'site': ticket.site_description, 'reply_message': reply.message, 'link': link,
        },
        [assignee.user.email],
    )


def _send_escalation_email(request, escalation):
    """Best-effort email to whoever a ticket was escalated to."""
    if not NotificationSettings.load().escalation_emails:  # the admin's Notifications page
        return
    recipient = escalation.escalated_to
    if not recipient.user_id or not recipient.user.email:
        return
    ticket = escalation.ticket
    link = staff_url(request, reverse('tasks:ticket_review', args=[ticket.pk]))
    with translation.override('en'):
        subject = _('Ticket escalated to you — %(number)s') % {'number': ticket.ticket_number}
        message = _(
            '%(by)s has escalated ticket %(number)s (%(company)s — %(site)s) to you for a decision:\n\n'
            '%(reason)s\n\n'
            'Please record your decision here:\n'
            '%(link)s\n',
        ) % {
            'by': escalation.escalated_by.full_name, 'number': ticket.ticket_number,
            'company': ticket.company_name, 'site': ticket.site_description, 'reason': escalation.reason,
            'link': link,
        }
    _send_notification_email(
        subject, message, 'tasks/email/ticket_escalated.html',
        {
            'escalated_by': escalation.escalated_by.full_name, 'ticket_number': ticket.ticket_number,
            'company': ticket.company_name, 'site': ticket.site_description, 'reason': escalation.reason,
            'link': link,
        },
        [recipient.user.email],
    )


def _send_escalation_decided_email(request, escalation):
    """Best-effort email back to whoever escalated, once it's decided."""
    if not NotificationSettings.load().escalation_emails:  # the admin's Notifications page
        return
    recipient = escalation.escalated_by
    if not recipient.user_id or not recipient.user.email:
        return
    ticket = escalation.ticket
    link = staff_url(request, reverse('tasks:ticket_review', args=[ticket.pk]))
    with translation.override('en'):
        subject = _('Escalation decided — %(number)s') % {'number': ticket.ticket_number}
        decision = escalation.get_decision_display()
        message = _(
            '%(by)s has issued a decision on the escalation of ticket %(number)s: %(decision)s.\n\n'
            '%(note)s\n\n'
            'Please continue processing the ticket here:\n'
            '%(link)s\n',
        ) % {
            'by': escalation.escalated_to.full_name, 'number': ticket.ticket_number, 'decision': decision,
            'note': escalation.decision_note, 'link': link,
        }
    _send_notification_email(
        subject, message, 'tasks/email/escalation_decided.html',
        {
            'decided_by': escalation.escalated_to.full_name, 'ticket_number': ticket.ticket_number,
            'decision': decision, 'note': escalation.decision_note, 'link': link,
        },
        [recipient.user.email],
    )


def _staff_notification_recipients(country, roles, everywhere=None):
    """Logins in this country, among these roles, with an email on file —
    who gets alerted about a new ticket or a new task. The bell only
    reaches someone already in the app; this reaches them either way.
    `everywhere`: a permission whose roles hear from every country, not
    only their own (a new ticket: tickets_all_countries).
    """
    from people.models import with_permission

    in_reach = Q(country=country)
    if everywhere:
        in_reach |= with_permission(everywhere)
    return list(
        # Only someone who can open tasks (a custom role may have that switched off).
        Technician.objects.filter(in_reach, with_permission(RolePermission.Permission.VIEW_TASKS),
                                  is_active=True, role__in=roles, user__isnull=False)
        .exclude(user__email='').values_list('user__email', flat=True).distinct()
    )


def send_new_ticket_email_to_staff(request, ticket):
    """Best-effort, same fail-silent pattern as every other notification
    here — the roles chosen on the admin's Notifications page (by default
    whoever can manage_tickets), in the ticket's country, alongside the bell (people.context_processors.notification_
    bell). Public (no leading underscore): customers.views.portal_ticket_
    new calls this directly, the same way it already calls save_new_ticket.
    """
    # The chosen roles (by default the Technical Support Manager and the country's
    # Country Support Manager); nobody of those: the admins (tasks.alarms).
    from tasks.alarms import new_ticket_people
    # Their phones too, opening the request in the app — whatever the email switch says.
    people = list(new_ticket_people(ticket.country))
    urgent = ticket.machines.filter(problem=TicketMachine.Problem.NOT_WORKING).exists()
    transaction.on_commit(lambda: [push_in_their_language(
        person,
        lambda: _('New request — a machine is not working') if urgent else _('New request'),
        lambda: _('%(number)s — %(company)s') % {'number': ticket.ticket_number, 'company': ticket.company_name},
        {'type': 'ticket', 'ticket_id': ticket.pk},
    ) for person in people])
    switches = NotificationSettings.load()
    if not switches.new_ticket_email:
        return
    recipients = list(
        new_ticket_people(ticket.country).exclude(user__email='').values_list('user__email', flat=True).distinct()
    )
    if not recipients:
        return
    link = staff_url(request, reverse('tasks:ticket_review', args=[ticket.pk]))
    with translation.override('en'):
        subject = _('New ticket %(number)s — %(company)s') % {
            'number': ticket.ticket_number, 'company': ticket.company_name,
        }
        message = _(
            'A new ticket has been received.\n\n'
            'Ticket: %(number)s\n'
            'Company: %(company)s\n'
            'Site: %(site)s\n'
            'Description: %(description)s\n\n'
            'Review the ticket:\n'
            '%(link)s\n',
        ) % {
            'number': ticket.ticket_number, 'company': ticket.company_name,
            'site': ticket.site_description, 'description': ticket.description, 'link': link,
        }
    _send_notification_email(
        subject, message, 'tasks/email/new_ticket_to_staff.html',
        {
            'ticket_number': ticket.ticket_number, 'company': ticket.company_name,
            'site': ticket.site_description, 'description': ticket.description, 'link': link,
        },
        recipients,
    )


def _send_new_task_email_to_staff(request, task):
    """Best-effort, same fail-silent pattern as every other notification
    here — the roles chosen on the admin's Notifications page (by default
    managers and admins), in the task's country, alongside the bell.
    """
    switches = NotificationSettings.load()
    if not switches.new_task_email:
        return
    recipients = _staff_notification_recipients(task.site.customer.country, switches.new_task_roles)
    if not recipients:
        return
    link = staff_url(request, reverse('tasks:task_detail', args=[task.pk]))
    with translation.override('en'):
        subject = _('New task %(number)s — %(site)s') % {'number': task.task_number, 'site': task.site.name}
        message = _(
            'A new task has been created.\n\n'
            'Task: %(number)s\n'
            'Site: %(site)s\n'
            'Description: %(description)s\n\n'
            'View the task:\n'
            '%(link)s\n',
        ) % {'number': task.task_number, 'site': task.site.name, 'description': task.description, 'link': link}
    _send_notification_email(
        subject, message, 'tasks/email/new_task_to_staff.html',
        {'task_number': task.task_number, 'site': task.site.name, 'description': task.description, 'link': link},
        recipients,
    )


# Defined with the pages in tasks.views; imported last, so either module can load first.
from .views import (  # noqa: E402
    customer_has_current_time, logger, schedule_change_pending,
)
