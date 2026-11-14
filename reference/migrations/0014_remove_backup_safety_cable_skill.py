from django.db import migrations

NAME = 'Install backup safety cable'
NAME_AR = 'تركيب نظام حبل/كابل الأمان الاحتياطي (Backup Safety Cable)'


def remove_skill(apps, schema_editor):
    apps.get_model('reference', 'Skill').objects.filter(name=NAME).delete()


def add_skill(apps, schema_editor):
    Skill = apps.get_model('reference', 'Skill')
    Skill.objects.get_or_create(
        name=NAME, defaults={'name_ar': NAME_AR, 'category': 'other', 'is_active': True},
    )


class Migration(migrations.Migration):

    dependencies = [
        ('reference', '0013_add_uniform_conduct_area'),
        # Deleting a skill reaches its technician skills: after their ordering stopped naming
        # the skill's brand (removed in reference 0010), whatever order a fresh database is built in.
        ('people', '0017_alter_technicianskill_options_and_more'),
    ]

    operations = [
        migrations.RunPython(remove_skill, add_skill),
    ]
