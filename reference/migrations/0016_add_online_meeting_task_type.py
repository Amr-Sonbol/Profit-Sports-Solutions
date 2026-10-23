from django.db import migrations

CODE = 'online_meeting'
NAME = 'Online Meeting'
NAME_AR = 'اجتماع عن بُعد'
CATEGORY = 'installation'  # same tier as Site Consultation / Assessment — advisory, not repair work


def add_online_meeting(apps, schema_editor):
    TaskType = apps.get_model('reference', 'TaskType')
    TaskType.objects.get_or_create(
        code=CODE, defaults={'name': NAME, 'name_ar': NAME_AR, 'category': CATEGORY},
    )


def remove_online_meeting(apps, schema_editor):
    apps.get_model('reference', 'TaskType').objects.filter(code=CODE).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('reference', '0015_remove_cable_kit_and_crimped_end_skills'),
    ]

    operations = [
        migrations.RunPython(add_online_meeting, remove_online_meeting),
    ]
