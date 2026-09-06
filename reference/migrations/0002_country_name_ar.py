from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reference', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='country',
            name='name_ar',
            field=models.CharField(default='', max_length=100, verbose_name='name (Arabic)'),
            preserve_default=False,
        ),
    ]
