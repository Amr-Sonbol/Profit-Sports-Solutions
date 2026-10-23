"""The visit report as a PDF on the company letterhead (WeasyPrint): what
the technician recorded, the machines and parts, and the customer's
sign-off. Rendered in the language asked for, right to left in Arabic.
"""
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.utils import timezone, translation

from tasks.models import TaskAssignment


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
    work done, the machines with their outcomes, the parts (no prices) and
    who signed. Frozen into WorkReport.signed_content at signing, so a
    later edit can be spotted by comparing this with the frozen copy.
    """
    task = report.task
    # Sorted, not in saved order: the report form deletes and recreates
    # these rows on every save, which mustn't read as a change.
    machines = sorted((
        {
            'name': f"{item.asset.brand.name if item.asset.brand else ''} {item.asset.model_name}".strip(),
            'serial': item.asset.serial_no,
            'outcome': item.outcome,
        }
        for item in task.task_assets.select_related('asset__brand')
    ), key=lambda machine: (machine['name'], machine['serial'], machine['outcome']))
    parts = sorted((
        {'code': part.part_code, 'description': part.description, 'quantity': part.quantity}
        for part in report.parts_used.all()
    ), key=lambda part: (part['code'], part['description'], part['quantity']))
    return {
        'findings': report.findings,
        'action_taken': report.action_taken,
        'resolved': report.resolved,
        'customer_name': report.customer_name,
        'machines': machines,
        'parts': parts,
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
    return signed != report_content(report)


def _template_context(report, content, signature_url, signed_at, changed):
    from tasks.models import TaskAsset

    task = report.task
    team = TaskAssignment.objects.filter(task=task, is_active=True).select_related('technician')
    outcomes = dict(TaskAsset.Outcome.choices)
    return {
        'report': report,
        'task': task,
        'site': task.site,
        'customer': task.site.customer,
        'team': [assignment.technician for assignment in sorted(team, key=lambda a: a.role != TaskAssignment.Role.LEAD)],
        'content': content,
        'work_lines': [line.strip(' -•\t') for line in content['action_taken'].splitlines() if line.strip(' -•\t')],
        'machines': [{**machine, 'outcome_label': outcomes.get(machine['outcome'], '')} for machine in content['machines']],
        'parts': content['parts'],
        'signature_url': signature_url,
        'signed_at': signed_at,
        'changed_after_signing': changed,
    }


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
