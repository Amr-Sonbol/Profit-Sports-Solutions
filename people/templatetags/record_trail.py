"""{% record_trail obj %}: who created and last changed a record, and (a task) what
was removed from it — at the foot of its page (spots.audit.trail). The Admin also
gets "Full history": the Audit log for this one record."""
from django import template
from django.urls import reverse

from spots.audit import trail, who

register = template.Library()


@register.inclusion_tag('_record_trail.html', takes_context=True)
def record_trail(context, obj, show_created=True):
    found = trail(obj)
    technician = getattr(context['request'].user, 'technician', None)
    is_admin = technician is not None and technician.role == technician.Role.ADMIN and not technician.custom_role_id
    return {
        'created': found['created'] if show_created else None, 'created_by': who(found['created']),
        'changed': found['changed'], 'changed_by': who(found['changed']),
        'changed_fields': ', '.join(found['changed'].changes) if found['changed'] else '',
        'removed': [{'what': entry.object_repr, 'by': who(entry), 'at': entry.at} for entry in found['removed']],
        'history_url': (
            f"{reverse('tasks:audit_log')}?kind={obj._meta.label_lower}&record={obj.pk}" if is_admin else ''
        ),
    }
