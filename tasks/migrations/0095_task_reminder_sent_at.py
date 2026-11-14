from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0094_how_work_is_done'),
    ]

    operations = [
        migrations.AddField(
            model_name='task',
            name='reminder_sent_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='customer reminded'),
        ),
    ]
