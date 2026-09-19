from django.db import migrations

NAME = 'Adherence to official uniform'
NAME_AR = 'الالتزام بالزي الرسمي'


def add_uniform_conduct_area(apps, schema_editor):
    ConductArea = apps.get_model('reference', 'ConductArea')
    ConductArea.objects.get_or_create(name=NAME, defaults={'name_ar': NAME_AR})


def remove_uniform_conduct_area(apps, schema_editor):
    apps.get_model('reference', 'ConductArea').objects.filter(name=NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('reference', '0012_add_elliptical_skills'),
    ]

    operations = [
        migrations.RunPython(add_uniform_conduct_area, remove_uniform_conduct_area),
    ]
