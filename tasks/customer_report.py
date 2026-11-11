"""What the customer reported, machine by machine — the one place that gathers
it, for every page that shows it (tasks/_customer_report.html: the request
page, the office's task page, the technician's) and the app (api: from_request):

- each machine: its name, serial and serial-plate photo, the problem, the
  customer's own words, and its photos and videos;
- files not tied to a machine;
- what the customer sent later, in the conversation (their files only —
  never support's messages).

A task split from a request shows only its own machines.
"""
from .models import TicketReply

IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'heic'}
VIDEO_EXTENSIONS = {'mp4', 'mov', 'webm'}


def file_kind(file):
    """'image', 'video' or 'file', from the stored name's extension."""
    extension = file.name.rsplit('.', 1)[-1].lower() if '.' in file.name else ''
    if extension in IMAGE_EXTENSIONS:
        return 'image'
    if extension in VIDEO_EXTENSIONS:
        return 'video'
    return 'file'


def _item(file, label='', added_by=None):
    return {'file': file, 'kind': file_kind(file), 'label': label, 'added_by': added_by}


def customer_report(ticket, task=None):
    """{'machines': [{'machine', 'items'}], 'other': [items], 'later': [items],
    'photos': n, 'videos': n} — items being {'file', 'kind', 'label', 'added_by'}."""
    machines = list(ticket.machines.select_related('asset').prefetch_related('attachments__added_by').order_by('position', 'pk'))
    if task is not None and any(machine.task_id == task.pk for machine in machines):
        machines = [machine for machine in machines if machine.task_id == task.pk]
    cards = []
    for machine in machines:
        items = []
        if machine.serial_photo:
            items.append(_item(machine.serial_photo, label='serial'))
        items += [_item(attachment.file, added_by=attachment.added_by)
                  for attachment in sorted(machine.attachments.all(), key=lambda a: a.uploaded_at)]
        cards.append({'machine': machine, 'items': items})
    other = [_item(attachment.file, added_by=attachment.added_by)
             for attachment in ticket.attachments.filter(machine__isnull=True).select_related('added_by').order_by('uploaded_at')]
    later = []
    for reply in ticket.replies.filter(sender=TicketReply.Sender.CUSTOMER).prefetch_related('files').order_by('sent_at'):
        files = ([reply.attachment] if reply.attachment else []) + [item.file for item in reply.files.all()]
        later += [dict(_item(file), sent_at=reply.sent_at) for file in files]
    every = [item for card in cards for item in card['items']] + other + later
    return {
        'machines': cards, 'other': other, 'later': later,
        'photos': sum(1 for item in every if item['kind'] == 'image'),
        'videos': sum(1 for item in every if item['kind'] == 'video'),
    }
