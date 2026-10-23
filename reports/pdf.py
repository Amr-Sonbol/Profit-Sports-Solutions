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


def visit_report_context(report):
    """What the report template shows, from the report and its task."""
    task = report.task
    team = TaskAssignment.objects.filter(task=task, is_active=True).select_related('technician')
    return {
        'report': report,
        'task': task,
        'site': task.site,
        'customer': task.site.customer,
        'team': [assignment.technician for assignment in sorted(team, key=lambda a: a.role != TaskAssignment.Role.LEAD)],
        'work_lines': [line.strip(' -•\t') for line in report.action_taken.splitlines() if line.strip(' -•\t')],
        'machines': task.task_assets.select_related('asset__brand').all(),
        'parts': report.parts_used.all(),
    }


def render_visit_report_pdf(report, language=None):
    """The report as PDF bytes, in `language` (default: the active one),
    with times in the site's own country time zone.
    """
    from weasyprint import HTML

    country_zone = report.task.site.customer.country.timezone
    with translation.override(language or translation.get_language()), timezone.override(country_zone):
        html = render_to_string('reports/visit_report_pdf.html', {
            **visit_report_context(report),
            'is_rtl': translation.get_language_bidi(),
        })
    return HTML(string=html, base_url='/', url_fetcher=_url_fetcher).write_pdf()
