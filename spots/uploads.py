"""Every uploaded file is what its name says it is.

FileExtensionValidator alone trusts the name: a web page or a program renamed
"photo.jpg" would be accepted. SafeFileValidator checks the name the same way
and then the content — cheaply, once, at upload:

- a photo (jpg, png, gif, webp) must open as a real image: Pillow reads its
  header and checks it (verify()) without decoding or redrawing the picture.
  Any of those kinds under any of those names — a screenshot (PNG) a phone
  app saved as "photo.jpg" is still a real photo;
- a PDF must start with "%PDF-"; a video or an iPhone photo (mp4, mov, heic)
  must carry its container's marker; a webm its own.

Used for every upload — customers' and staff's, website and app — in place of
FileExtensionValidator. The file is left exactly as it was sent.
"""
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext_lazy as _

IMAGE_FORMATS = {'jpg': 'JPEG', 'jpeg': 'JPEG', 'png': 'PNG', 'gif': 'GIF', 'webp': 'WEBP'}
# ISO media files (mp4, mov, heic) name their first box in bytes 4–8: "ftyp" for any
# recent one; older QuickTime files may start straight with one of the others.
ISO_BOXES = {b'ftyp', b'moov', b'mdat', b'wide', b'free', b'skip', b'pnot'}
WEBM_MAGIC = b'\x1aE\xdf\xa3'


def content_matches(upload, extension):
    """Whether the file's content is what `extension` says. Unknown kinds: True
    (the extension list alone decides those)."""
    position = upload.tell() if hasattr(upload, 'tell') else 0
    try:
        upload.seek(0)
        if extension in IMAGE_FORMATS:
            from PIL import Image

            with Image.open(upload) as image:
                image.verify()
                return image.format in set(IMAGE_FORMATS.values())
        head = upload.read(1024)
        if extension == 'pdf':
            return b'%PDF-' in head
        if extension in ('mp4', 'mov', 'heic'):
            return head[4:8] in ISO_BOXES
        if extension == 'webm':
            return head.startswith(WEBM_MAGIC)
        return True
    except Exception:
        # Pillow refusing it, or anything unreadable: not the file it claims to be.
        return False
    finally:
        upload.seek(position)


@deconstructible
class SafeFileValidator(FileExtensionValidator):
    """FileExtensionValidator, plus: the content is what the extension says."""

    content_message = _(
        '“%(name)s” could not be uploaded because it is not a valid photo, video or PDF. '
        'Please select the original file from your device and try again.'
    )

    def __call__(self, value):
        super().__call__(value)
        extension = value.name.rsplit('.', 1)[-1].lower() if '.' in value.name else ''
        # A stored file (already accepted, being saved again) isn't opened again.
        if getattr(value, '_committed', False):
            return
        if not content_matches(value, extension):
            raise ValidationError(
                self.content_message, code='invalid_content',
                params={'name': value.name.rsplit('/', 1)[-1], 'extension': extension.upper()},
            )
