import os
from urllib.parse import unquote, urlparse

from django import template

register = template.Library()


@register.filter
def filename(file):
    """A file's own name, without the folders (and the random one) it's kept
    under: "ticket_attachments/3f9c…/IMG_0001.jpg" → "IMG_0001.jpg". Takes a
    stored file or a file's address (a task attachment keeps its URL)."""
    path = getattr(file, 'name', None) or urlparse(str(file or '')).path
    return unquote(os.path.basename(path))
