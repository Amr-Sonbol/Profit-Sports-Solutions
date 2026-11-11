from django.db import migrations, models


def to_one_kind(apps, schema_editor):
    """Stock requests, vendor follow-ups and other internal tickets all become plain internal tasks."""
    apps.get_model('tasks', 'CustomerTicket').objects.filter(
        kind__in=['stock', 'vendor', 'other'],
    ).update(kind='internal')


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0082_task_event_stepped_in'),
    ]

    operations = [
        migrations.AlterField(
            model_name='customerticket',
            name='kind',
            field=models.CharField(choices=[('customer', 'Customer'), ('internal', 'Internal task')], default='customer', max_length=10, verbose_name='type'),
        ),
        migrations.RunPython(to_one_kind, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='customerticket',
            name='subject',
            field=models.CharField(blank=True, help_text="an internal task's subject; on a customer's request, what it's about (Other), the area (Flooring) or the items (Accessories)", max_length=200, verbose_name='subject'),
        ),
    ]
