"""Tiny real files for tests that upload one: uploads are checked for being
what their name says (spots.uploads.SafeFileValidator), so a test's photo
has to be a real photo, not placeholder bytes."""
import io

from PIL import Image


def _image(fmt):
    buffer = io.BytesIO()
    Image.new('RGB', (8, 8), (200, 80, 40)).save(buffer, fmt)
    return buffer.getvalue()


REAL_JPEG = _image('JPEG')
REAL_PNG = _image('PNG')
# The start of an MP4 file: its "ftyp" box naming the brand. Enough to be one.
REAL_MP4 = b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom' + b'\x00' * 32
REAL_PDF = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n'
