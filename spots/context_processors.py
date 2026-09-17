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
