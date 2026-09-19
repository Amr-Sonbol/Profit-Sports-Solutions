from django.db import migrations

# Adds a third cardio equipment group alongside treadmill/bike, the same
# way 0005_cardio_lines.py added onto 0003's original seed.
ELLIPTICAL_SKILLS = [
    # (name, name_ar)
    ('Elliptical — check transmission belt', 'إليبتيكال: فحص حزام النقل (Transmission Belt)'),
    (
        'Elliptical — clean ventilation fan and manual pulse sensors',
        'إليبتيكال: تنظيف مروحة التهوية وحساسات النبض اليدوية',
    ),
    (
        'Elliptical — operational check of footplates and handgrips',
        'إليبتيكال: الفحص التشغيلي لوقفات القدم والمقابض (Footplates & Handgrips)',
    ),
    (
        'Elliptical — diagnose and replace rear flywheel and its bearing',
        'إليبتيكال: تشخيص واستبدال الفلايويل الخلفي وجلبته',
    ),
]


def add_elliptical_skills(apps, schema_editor):
    Skill = apps.get_model('reference', 'Skill')
    for name, name_ar in ELLIPTICAL_SKILLS:
        Skill.objects.create(name=name, name_ar=name_ar, category='cardio', is_active=True)


def remove_elliptical_skills(apps, schema_editor):
    Skill = apps.get_model('reference', 'Skill')
    Skill.objects.filter(name__in=[name for name, _name_ar in ELLIPTICAL_SKILLS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('reference', '0011_reseed_skills_as_repair_tasks'),
    ]

    operations = [
        migrations.RunPython(add_elliptical_skills, remove_elliptical_skills),
    ]
