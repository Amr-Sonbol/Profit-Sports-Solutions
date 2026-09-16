from django.db import migrations

# (name, name_ar)
CONDUCT_AREAS = [
    ('Cleanliness & site care', 'النظافة والعناية بالموقع'),
    ('Professional appearance & conduct', 'المظهر والسلوك المهني'),
    ('Rule & procedure adherence', 'الالتزام بالقواعد والإجراءات'),
    ('Punctuality & communication', 'الالتزام بالمواعيد والتواصل'),
    ('Tool & vehicle care', 'العناية بالأدوات والمركبة'),
]


def seed(apps, schema_editor):
    ConductArea = apps.get_model('reference', 'ConductArea')
    for name, name_ar in CONDUCT_AREAS:
        ConductArea.objects.get_or_create(name=name, defaults={'name_ar': name_ar})


def unseed(apps, schema_editor):
    apps.get_model('reference', 'ConductArea').objects.filter(
        name__in=[name for name, _name_ar in CONDUCT_AREAS],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('reference', '0006_conductarea'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
