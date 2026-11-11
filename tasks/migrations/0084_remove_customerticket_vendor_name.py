from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0083_internal_task_one_kind'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='customerticket',
            name='vendor_name',
        ),
    ]
