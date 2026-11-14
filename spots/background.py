"""Work done after a request, so nobody waits for it: emails, report PDFs, phone
notifications. Started only once the request's changes are saved (nothing is
sent for a change that was rolled back — a report's "Review" step, a refused
form), on a small shared pool — at most BACKGROUND_WORKERS at once (two), so a
burst of PDFs can't run the server out of memory; the rest wait their turn.
At once, in the request, under the test runner (settings.SEND_EMAILS_IN_BACKGROUND).
"""
import logging
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.db import connection, transaction

logger = logging.getLogger(__name__)

_pool = ThreadPoolExecutor(max_workers=getattr(settings, 'BACKGROUND_WORKERS', 2), thread_name_prefix='background')


def in_background(work):
    """Run `work` after the request's changes are saved, on the pool. Returns what
    `work` returned when run at once (tests), else None."""
    if not settings.SEND_EMAILS_IN_BACKGROUND:
        return work()

    def run():
        try:
            work()
        except Exception:  # noqa: BLE001 — nobody is waiting for it; logged, so it reaches Sentry
            logger.exception('Background work failed')
        finally:
            connection.close()  # the thread's own database connection

    transaction.on_commit(lambda: _pool.submit(run))
    return None
