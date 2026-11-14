from django.db import migrations


def fault_to_before(apps, schema_editor):
    """A "Fault" photo and a "Before" one showed the same thing: they're one kind now."""
    apps.get_model('tasks', 'TaskAttachment').objects.filter(purpose='fault').update(purpose='before')


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0086_safe_file_validator'),
    ]

    operations = [
        migrations.RunPython(fault_to_before, migrations.RunPython.noop),
    ]
