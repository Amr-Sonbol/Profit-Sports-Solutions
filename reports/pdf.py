"""The visit report as a PDF on the company letterhead (WeasyPrint): what
the technician recorded, the machines and parts, and the customer's
sign-off. Rendered in the language asked for, right to left in Arabic.
"""
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.staticfiles import finders
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone, translation

from .phrases import Action, Finding, Recommendation, WorkLine


def _local_file(url):
    """The file on disk behind a /media/ or /static/ URL, or None.

    The PDF only ever needs the app's own images (letterhead, fonts, the
    signature), so nothing else is fetched: no network requests from the
    server on behalf of a document.
    """
    path = urlparse(url).path
    media_prefix = '/' + settings.MEDIA_URL.strip('/') + '/'
    static_prefix = '/' + settings.STATIC_URL.strip('/') + '/'
    if path.startswith(media_prefix):
        media_root = Path(settings.MEDIA_ROOT).resolve()
        candidate = (media_root / path[len(media_prefix):]).resolve()
        return candidate if candidate.is_file() and media_root in candidate.parents else None
    if path.startswith(static_prefix):
        found = finders.find(path[len(static_prefix):])
        return Path(found) if found else None
    return None


def _url_fetcher(url, *args, **kwargs):
    from weasyprint.urls import URLFetcherResponse

    local = _local_file(url)
    if local is None:
        raise ValueError(f'Not a local file: {url}')
    return URLFetcherResponse(url, body=local.read_bytes())


def report_content(report):
    """What the customer sees and signs, as plain JSON-safe data: findings,
    work done, the time on site, the machines with their outcomes, the
    parts (no prices) and who signed. Frozen into WorkReport.signed_content at signing, so a
    later edit can be spotted by comparing this with the frozen copy.
    """
    task = report.task
    # Sorted, not in saved order: the report form deletes and recreates
    # these rows on every save, which mustn't read as a change.
    # A machine's finding only when there is one: reports signed before
    # findings existed must still read as unchanged.
    machines = sorted((
        {
            'name': f"{item.asset.brand.name if item.asset.brand else ''} {item.asset.model_name}".strip(),
            'serial': item.asset.serial_no,
            'outcome': item.outcome,
            **({'note': item.note} if item.note else {}),
            # The standard wording and the action, likewise only when set.
            **({'finding_code': item.finding_code} if item.finding_code else {}),
            **({'action_code': item.action_code} if item.action_code else {}),
            **({'action': item.action} if item.action else {}),
        }
        for item in task.task_assets.select_related('asset__brand')
    ), key=lambda machine: (machine['name'], machine['serial'], machine['outcome'], machine.get('note', '')))
    parts = sorted((
        {'code': part.part_code, 'description': part.description, 'quantity': part.quantity}
        for part in report.parts_used.all()
    ), key=lambda part: (part['code'], part['description'], part['quantity']))
    # The standard work lines and recommendations, only when there are
    # any — reports signed before they existed still read as unchanged.
    extras = {
        key: value for key, value in (
            ('work_lines', list(report.work_lines)), ('recommendations', list(report.recommendations)),
            ('recommendation', report.recommendation),
        ) if value
    }
    return {
        'findings': report.findings,
        'action_taken': report.action_taken,
        'resolved': report.resolved,
        'hours': f'{report.labour_hours:.2f}',
        'customer_name': report.customer_name,
        'machines': machines,
        'parts': parts,
        **extras,
    }


def freeze_signed_content(report):
    """Keep exactly what the customer signed, with their signature and the
    time, on the report. Called when a signature is taken (the first
    filing, or the customer signing again on a correction).
    """
    report.signed_content = {**report_content(report), 'signature_url': report.signature_url}
    report.signed_at = report.submitted_at
    report.save(update_fields=['signed_content', 'signed_at'])


def changed_after_signing(report):
    """True when what the report says now differs from what the customer signed."""
    if not report.signed_content:
        return False
    signed = {key: value for key, value in report.signed_content.items() if key != 'signature_url'}
    current = report_content(report)
    if 'hours' not in signed:
        current.pop('hours')  # signed before the report showed the time on site
    return signed != current


def _hours_text(hours):
    """'2.50' -> '2.5', '3.00' -> '3'; '' for none or zero."""
    if not hours or Decimal(hours) == 0:
        return ''
    return f'{Decimal(hours):f}'.rstrip('0').rstrip('.') if '.' in hours else hours


def _next_visit(task):
    """The site's next booked task after this one, as a date — the
    report's "Next visit". None when nothing is booked yet."""
    from tasks.models import Task

    day = timezone.localtime(task.scheduled_for).date() if task.scheduled_for else timezone.localdate()
    upcoming = Task.objects.filter(site=task.site).exclude(pk=task.pk).exclude(
        status__in=[Task.Status.CANCELLED, Task.Status.CLOSED],
    ).filter(Q(scheduled_for__date__gt=day) | Q(scheduled_for__isnull=True, scheduled_date__gt=day))
    dates = [
        timezone.localtime(other.scheduled_for).date() if other.scheduled_for else other.scheduled_date
        for other in upcoming.only('scheduled_for', 'scheduled_date')
    ]
    return min(dates) if dates else None


def _template_context(report, content, signature_url, signed_at, changed):
    from tasks.models import TaskAsset

    task = report.task
    outcomes = dict(TaskAsset.Outcome.choices)
    findings, actions = dict(Finding.choices), dict(Action.choices)
    free_lines = [line.strip(' -•\t') for line in content['action_taken'].splitlines() if line.strip(' -•\t')]
    standard_lines = dict(WorkLine.choices)
    return {
        'report': report,
        'task': task,
        'site': task.site,
        'customer': task.site.customer,
        'content': content,
        # The standard lines first (in the report's language), then any typed.
        'work_lines': [standard_lines[code] for code in content.get('work_lines', []) if code in standard_lines] + free_lines,
        'machines': [
            {
                **machine,
                'outcome_label': outcomes.get(machine['outcome'], ''),
                'finding_label': findings.get(machine.get('finding_code'), machine.get('note', '')),
                'finding_extra': machine.get('note', '') if machine.get('finding_code') else '',
                'action_label': actions.get(machine.get('action_code'), machine.get('action', '')),
                'action_extra': machine.get('action', '') if machine.get('action_code') else '',
            }
            for machine in content['machines']
        ],
        'recommendations': [
            dict(Recommendation.choices)[code] for code in content.get('recommendations', [])
            if code in dict(Recommendation.choices)
        ] + ([content['recommendation']] if content.get('recommendation') else []),
        'next_visit': _next_visit(task),
        # The time on site as "2.5", "3" — none on reports signed before it
        # was shown, or with no taps to work it out from.
        'hours': _hours_text(content.get('hours')),
        'parts': content['parts'],
        'signature_url': signature_url,
        'signed_at': signed_at,
        'changed_after_signing': changed,
    }


def report_language(customer):
    """The language a customer's visit reports are in: always their
    country's (Country.report_language) — Arabic in Saudi Arabia and Egypt,
    English everywhere else, whatever the customer picked for themselves.
    The emailed copy and its email, and every download not asking for a
    language (?lang=), use this.
    """
    return customer.country.report_language or 'en'


def render_visit_report_html(report, language=None, problems=()):
    """The report as it would be sent, as an HTML page with a "preview"
    banner and the things to fix (`problems`) above it — the review step
    before sending. Same template, content and language as the PDF.
    """
    context = _template_context(report, report_content(report), report.signature_url, report.submitted_at, False)
    country_zone = report.task.site.customer.country.timezone
    with translation.override(language or translation.get_language()), timezone.override(country_zone):
        return render_to_string('reports/visit_report_pdf.html', {
            **context,
            'is_rtl': translation.get_language_bidi(),
            'preview': True,
            'problems': [str(problem) for problem in problems],
        })


def render_visit_report_pdf(report, language=None, signed_version=False):
    """The report as PDF bytes, in `language` (default: the active one),
    with times in the site's own country time zone. With signed_version,
    the frozen copy of what the customer signed instead of the report as
    it is now; the current version says so when it has changed since.
    """
    from weasyprint import HTML

    if signed_version and report.signed_content:
        context = _template_context(
            report, report.signed_content, report.signed_content.get('signature_url', ''), report.signed_at, False,
        )
    else:
        context = _template_context(
            report, report_content(report), report.signature_url, report.signed_at or report.submitted_at,
            changed_after_signing(report),
        )
    country_zone = report.task.site.customer.country.timezone
    with translation.override(language or translation.get_language()), timezone.override(country_zone):
        html = render_to_string('reports/visit_report_pdf.html', {
            **context,
            'is_rtl': translation.get_language_bidi(),
        })
    return HTML(string=html, base_url='/', url_fetcher=_url_fetcher).write_pdf()
