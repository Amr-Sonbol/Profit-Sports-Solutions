from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reference', '0017_alter_skill_category'),
    ]

    operations = [
        migrations.AddField(
            model_name='country',
            name='weekend_days',
            field=models.CharField(
                default='4,5', max_length=13, verbose_name='weekend days',
                help_text='comma-separated weekday numbers, Monday = 0 — e.g. 4,5 for Friday–Saturday',
            ),
        ),
    ]
