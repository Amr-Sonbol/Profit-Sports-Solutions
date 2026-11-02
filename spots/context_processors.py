import os

from django.conf import settings

_BASE_CSS_PATH = settings.BASE_DIR / 'static' / 'css' / 'base.css'


def static_version(request):
    """The CSS file's own last-modified time, appended to its URL as a
    cache-buster (?v=...). Editing base.css during development was
    repeatedly invisible in a browser that had already cached the old
    file — this forces every real change to be a new URL, no manual
    Ctrl+Shift+R required.
    """
    try:
        version = int(os.path.getmtime(_BASE_CSS_PATH))
    except OSError:
        version = 0
    return {'css_version': version}


# Pages with nowhere to go back to: the logins, the one-time account
# setup, and the portal's own top pages (its header links).
NO_BACK_LINK = {
    'login', 'portal_login', 'portal_first_login', 'portal_account_link', 'portal_home', 'portal_requests',
    'portal_visits', 'portal_history', 'portal_machines', 'portal_settings',
}


def portal_nav(request):
    """A customer's header links — Home, Requests, Visits, History,
    Machines, Settings — with the current section marked: a request's or a
    machine's own page counts as its list's (a visit's page is marked by
    the view, upcoming or history)."""
    if not hasattr(getattr(request, 'user', None), 'customer'):
        return {}
    from django.urls import reverse
    from django.utils.translation import gettext as _

    match = request.resolver_match
    name = match.url_name if match else ''
    sections = [
        ('customers:portal_home', _('Home'), 'home', {'portal_home'}),
        ('customers:portal_requests', _('Requests'), 'ticket', {'portal_requests', 'portal_ticket_new', 'ticket_status'}),
        ('customers:portal_visits', _('Visits'), 'calendar', {'portal_visits'}),
        ('customers:portal_history', _('History'), 'file-text', {'portal_history'}),
        ('customers:portal_machines', _('Machines'), 'dumbbell', {'portal_machines', 'portal_machine'}),
        ('customers:portal_settings', _('Settings'), 'user', {'portal_settings'}),
    ]
    return {
        'portal_nav': [
            {'url': reverse(url_name), 'label': label, 'icon': icon, 'active': name in names}
            for url_name, label, icon, names in sections
        ],
        'portal_bell': portal_bell(request.user.customer),
    }


def portal_bell(customer):
    """The customer's unseen notifications (customers.notify), newest first,
    each as the words, what it's about and where it leads."""
    from django.urls import reverse
    from django.utils.translation import gettext as _

    from django.utils.formats import date_format

    from customers.models import CustomerNotification
    from customers.progress import visit_day

    items = []
    for note in customer.notifications.filter(seen_at__isnull=True).select_related('task__site', 'ticket')[:20]:
        if note.kind == CustomerNotification.Kind.REPLY and note.ticket_id:
            items.append({
                'text': _('New reply from our team'), 'about': note.ticket.ticket_number,
                'url': reverse('tasks:ticket_status', args=[note.ticket.token]),
            })
        elif note.task_id:
            if note.kind == CustomerNotification.Kind.REPORT_READY:
                text, anchor = _('Your visit report is ready — please rate the visit'), '#rate'
            else:
                day = visit_day(note.task)
                text = _('Visit booked for %(date)s') % {'date': date_format(day, 'j F')} if day else _('Visit booked')
                anchor = ''
            items.append({
                'text': text, 'about': note.task.site.name,
                'url': reverse('customers:portal_visit', args=[note.task_id]) + anchor,
            })
        else:
            continue
        items[-1]['created_at'] = note.created_at
    return items


def page_back(request):
    """Whether base.html shows its one-tap "Back" link on this page."""
    match = request.resolver_match
    return {'show_page_back': request.path != '/' and not (match and match.url_name in NO_BACK_LINK)}
