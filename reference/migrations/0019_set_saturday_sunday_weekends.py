from django.db import migrations

# Every other seeded country keeps the Friday–Saturday default.
SATURDAY_SUNDAY_COUNTRIES = ['AE', 'US', 'CA']


def set_weekends(apps, schema_editor):
    Country = apps.get_model('reference', 'Country')
    Country.objects.filter(iso_code__in=SATURDAY_SUNDAY_COUNTRIES).update(weekend_days='5,6')


def unset_weekends(apps, schema_editor):
    Country = apps.get_model('reference', 'Country')
    Country.objects.filter(iso_code__in=SATURDAY_SUNDAY_COUNTRIES).update(weekend_days='4,5')


class Migration(migrations.Migration):

    dependencies = [
        ('reference', '0018_country_weekend_days'),
    ]

    operations = [
        migrations.RunPython(set_weekends, unset_weekends),
    ]
