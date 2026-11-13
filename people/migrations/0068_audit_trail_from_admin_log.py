from django.db import migrations

# LogEntry's action flags (django.contrib.admin.models): addition, change, deletion.
ACTIONS = {1: 'created', 2: 'account', 3: 'deleted'}


def copy_admin_log(apps, schema_editor):
    """The history so far — account actions and the admin site's — into the audit trail."""
    LogEntry = apps.get_model('admin', 'LogEntry')
    AuditEntry = apps.get_model('people', 'AuditEntry')
    Technician = apps.get_model('people', 'Technician')
    names = dict(Technician.objects.exclude(user=None).values_list('user_id', 'full_name'))
    batch = []
    for entry in LogEntry.objects.select_related('user', 'content_type').iterator():
        batch.append(AuditEntry(
            at=entry.action_time, actor_id=entry.user_id,
            actor_name=(names.get(entry.user_id) or entry.user.username)[:150],
            action=ACTIONS.get(entry.action_flag, 'account'),
            object_type=f'{entry.content_type.app_label}.{entry.content_type.model}' if entry.content_type else '',
            object_id=entry.object_id or '', object_repr=entry.object_repr[:200], message=entry.change_message,
        ))
        if len(batch) >= 1000:
            AuditEntry.objects.bulk_create(batch)
            batch = []
    AuditEntry.objects.bulk_create(batch)


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0067_audit_trail'),
        ('admin', '0003_logentry_add_action_flag_choices'),
    ]

    operations = [
        migrations.RunPython(copy_admin_log, migrations.RunPython.noop),
    ]
