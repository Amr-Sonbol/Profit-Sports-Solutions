from django.db import migrations

SKILLS = [
    # (name, name_ar)
    ('Replace cable kit', 'استبدال طقم الكابل (Cable Kit)'),
    ('Make/replace crimped-end cable', 'عمل/استبدال الكابل بطرف مكرمب (Crimped End Cable)'),
]


def remove_skills(apps, schema_editor):
    Skill = apps.get_model('reference', 'Skill')
    Skill.objects.filter(name__in=[name for name, _name_ar in SKILLS]).delete()


def add_skills(apps, schema_editor):
    Skill = apps.get_model('reference', 'Skill')
    for name, name_ar in SKILLS:
        Skill.objects.get_or_create(
            name=name, defaults={'name_ar': name_ar, 'category': 'other', 'is_active': True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('reference', '0014_remove_backup_safety_cable_skill'),
    ]

    operations = [
        migrations.RunPython(remove_skills, add_skills),
    ]
