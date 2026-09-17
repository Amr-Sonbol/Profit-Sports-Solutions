from django.db import migrations

# (iso_code, task_prefix) — the company doesn't use ISO codes at all;
# every country gets its own locally-used abbreviation for task numbers.
TASK_PREFIXES = [
    ('EG', 'EGY'),
    ('BH', 'BAH'),
    ('QA', 'QAT'),
    ('AE', 'UAE'),
    ('SA', 'KSA'),
    ('OM', 'OMN'),
    ('US', 'USA'),
    ('CA', 'CAN'),
    ('KW', 'KWT'),
]


def seed(apps, schema_editor):
    Country = apps.get_model('reference', 'Country')
    for iso_code, task_prefix in TASK_PREFIXES:
        Country.objects.filter(iso_code=iso_code).update(task_prefix=task_prefix)


def unseed(apps, schema_editor):
    Country = apps.get_model('reference', 'Country')
    for iso_code, _task_prefix in TASK_PREFIXES:
        Country.objects.filter(iso_code=iso_code).update(task_prefix='')


class Migration(migrations.Migration):

    dependencies = [
        ('reference', '0008_country_task_prefix'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
