"""The monthly operations report for management: one month's headline
figures against the month before, and the breakdowns behind them. Every
figure is company-wide (all countries), counted by when things happened:
requests by when they were sent, tasks by when they were opened, visits by
when their report was filed, ratings by when the customer gave them.
"""
from datetime import datetime

from django.db.models import Avg, Count, F
from django.utils import timezone
from django.utils.translation import gettext as _, gettext_lazy

from reference.models import Country, TaskType
from reports.models import CustomerFeedback, WorkReport

from .models import CustomerTicket, Task

DONE = (Task.Status.PENDING_SUPERVISOR_REVIEW, Task.Status.COMPLETED, Task.Status.CLOSED)


def month_window(first_of_month):
    """Aware [start, end) datetimes covering one calendar month."""
    following = first_of_month.replace(year=first_of_month.year + first_of_month.month // 12,
                                       month=first_of_month.month % 12 + 1)
    start = timezone.make_aware(datetime.combine(first_of_month, datetime.min.time()))
    end = timezone.make_aware(datetime.combine(following, datetime.min.time()))
    return start, end


def _days(duration):
    return round(duration.total_seconds() / 86400, 1) if duration is not None else None


def _percent(part, whole):
    return round(100 * part / whole) if whole else None


def _querysets(first_of_month):
    start, end = month_window(first_of_month)
    return {
        'requests': CustomerTicket.objects.filter(
            kind=CustomerTicket.Kind.CUSTOMER, submitted_at__gte=start, submitted_at__lt=end,
        ),
        'tasks': Task.objects.filter(reported_at__gte=start, reported_at__lt=end),
        'visits': WorkReport.objects.filter(submitted_at__gte=start, submitted_at__lt=end),
        'ratings': CustomerFeedback.objects.filter(
            submitted_at__gte=start, submitted_at__lt=end, rating__isnull=False,
        ),
    }


def _headline(sets):
    """The month's key figures, as plain numbers (None where nothing to measure)."""
    tasks, visits, ratings = sets['tasks'], sets['visits'], sets['ratings']
    visit_count = visits.count()
    rating = ratings.aggregate(average=Avg('rating'), count=Count('pk'))
    return {
        'requests': sets['requests'].count(),
        'tasks': tasks.count(),
        'visits': visit_count,
        'first_visit': _percent(visits.filter(resolved=True).count(), visit_count),
        'completion': _percent(
            tasks.filter(status__in=DONE).count(), tasks.exclude(status=Task.Status.CANCELLED).count(),
        ),
        'days_to_visit': _days(tasks.filter(scheduled_for__isnull=False).aggregate(
            average=Avg(F('scheduled_for') - F('reported_at')),
        )['average']),
        'days_to_done': _days(visits.aggregate(average=Avg(F('submitted_at') - F('task__reported_at')))['average']),
        'rating': round(rating['average'], 1) if rating['average'] is not None else None,
        'rating_count': rating['count'],
    }


# Each headline figure: its label, how it's shown, and whether a rise is good.
HEADLINES = [
    ('requests', gettext_lazy('Requests received'), '{}', None),
    ('tasks', gettext_lazy('Tasks opened'), '{}', None),
    ('visits', gettext_lazy('Visits completed'), '{}', True),
    ('first_visit', gettext_lazy('Resolved on the first visit'), '{}%', True),
    ('completion', gettext_lazy("Completion rate of the month's tasks"), '{}%', True),
    ('days_to_visit', gettext_lazy('Average days from request to visit'), '{}', False),
    ('days_to_done', gettext_lazy('Average days to completion'), '{}', False),
    ('rating', gettext_lazy('Customer satisfaction (out of 5)'), '{}', True),
]


def _headline_cards(now, before):
    cards = []
    for key, label, shape, rise_is_good in HEADLINES:
        value, previous = now[key], before[key]
        change = trend = None
        if value is not None and previous is not None and value != previous:
            difference = round(value - previous, 1)
            change = shape.format(f'{difference:+g}')
            trend = 'up' if difference > 0 else 'down'
        good = None if trend is None or rise_is_good is None else (trend == 'up') == rise_is_good
        cards.append({
            'label': label, 'value': shape.format(value) if value is not None else '—',
            'change': change, 'trend': trend, 'good': good,
        })
    return cards


def _by_country(sets):
    """Per country: requests, tasks, visits completed, completion rate, satisfaction."""
    def counts(queryset, path):
        return {row[path]: row['n'] for row in queryset.values(path).annotate(n=Count('pk')).order_by()}

    tasks = sets['tasks']
    requests = counts(sets['requests'], 'country')
    opened = counts(tasks.exclude(status=Task.Status.CANCELLED), 'site__customer__country')
    done = counts(tasks.filter(status__in=DONE), 'site__customer__country')
    all_tasks = counts(tasks, 'site__customer__country')
    visits = counts(sets['visits'], 'task__site__customer__country')
    ratings = {
        row['task__site__customer__country']: row['average']
        for row in sets['ratings'].values('task__site__customer__country').annotate(average=Avg('rating')).order_by()
    }
    rows = []
    for country in Country.objects.filter(pk__in=set(requests) | set(all_tasks) | set(visits)).order_by('name'):
        rows.append({
            'country': country.display_name, 'requests': requests.get(country.pk, 0),
            'tasks': all_tasks.get(country.pk, 0), 'visits': visits.get(country.pk, 0),
            'completion': _percent(done.get(country.pk, 0), opened.get(country.pk, 0)),
            'rating': round(ratings[country.pk], 1) if ratings.get(country.pk) is not None else None,
        })
    return rows


def _request_grid(requests):
    """Requests as a grid: what they're about (rows) by what they need (columns)."""
    cells = {(row['about'], row['need']): row['n'] for row in requests.values('about', 'need').annotate(n=Count('pk'))}
    needs = CustomerTicket.Need.choices
    rows = []
    for about, about_label in CustomerTicket.About.choices:
        values = [cells.get((about, need), 0) for need, _label in needs]
        rows.append({'label': about_label, 'values': values, 'total': sum(values)})
    totals = [sum(row['values'][i] for row in rows) for i in range(len(needs))]
    return {'needs': [label for _value, label in needs], 'rows': rows, 'totals': totals, 'total': sum(totals)}


def _work_by_type(tasks):
    types = {task_type.pk: task_type for task_type in TaskType.objects.all()}
    return [
        {
            'label': types[row['task_type']].display_name if row['task_type'] in types else _('Not set'),
            'count': row['n'],
        }
        for row in tasks.values('task_type').annotate(n=Count('pk')).order_by('-n')
    ]


def _rating_spread(ratings):
    counts = {row['rating']: row['n'] for row in ratings.values('rating').annotate(n=Count('pk'))}
    total = sum(counts.values())
    return [
        {'stars': stars, 'count': counts.get(stars, 0), 'percent': _percent(counts.get(stars, 0), total) or 0}
        for stars in (5, 4, 3, 2, 1)
    ]


def _top_customers(sets, limit=10):
    activity = {}
    for row in sets['requests'].values('customer__name').annotate(n=Count('pk')):
        if row['customer__name']:
            activity.setdefault(row['customer__name'], [0, 0])[0] += row['n']
    for row in sets['tasks'].values('site__customer__name').annotate(n=Count('pk')):
        activity.setdefault(row['site__customer__name'], [0, 0])[1] += row['n']
    ranked = sorted(activity.items(), key=lambda item: -(item[1][0] + item[1][1]))[:limit]
    return [{'name': name, 'requests': requests, 'tasks': tasks, 'total': requests + tasks}
            for name, (requests, tasks) in ranked]


def _still_open(tasks):
    labels = dict(Task.Status.choices)
    return [
        {'label': labels.get(row['status'], row['status']), 'count': row['n']}
        for row in tasks.exclude(status__in=DONE + (Task.Status.CANCELLED,)).values('status')
        .annotate(n=Count('pk')).order_by('-n')
    ]


def month_report(first_of_month, previous_month):
    """Everything the report shows for one month."""
    sets = _querysets(first_of_month)
    now = _headline(sets)
    return {
        'headlines': _headline_cards(now, _headline(_querysets(previous_month))),
        'rating_count': now['rating_count'],
        'by_country': _by_country(sets),
        'request_grid': _request_grid(sets['requests']),
        'work_by_type': _work_by_type(sets['tasks']),
        'rating_spread': _rating_spread(sets['ratings']),
        'top_customers': _top_customers(sets),
        'still_open': _still_open(sets['tasks']),
    }
