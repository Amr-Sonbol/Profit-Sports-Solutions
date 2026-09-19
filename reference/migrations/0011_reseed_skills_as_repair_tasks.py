from django.db import migrations

# Replaces the old brand-based skill list entirely — a technician is now
# rated on the specific repair task, not "how good are they at Panatta".
# Basic level: every technician needs these, on any brand's equipment.
BASIC_SKILLS = [
    # (name, name_ar)
    ('Daily visual inspection (cables, belts, fasteners)', 'الفحص البصري اليومي (كابلات، سيور، لواصق)'),
    ('Clean and lubricate sliding guides', 'تنظيف وتشحيم القضبان المنزلقة'),
    ('Replace bolts', 'استبدال المسامير'),
    ('Replace pins', 'استبدال البنّات (Pins)'),
    ('Replace rubbers', 'استبدال المطاطات (Rubbers)'),
    ('Replace covers', 'استبدال الأغطية/الكفرات (Covers)'),
    ('Replace padding (full piece)', 'استبدال البطانة (Padding) - قطعة كاملة'),
    ('Replace holders', 'استبدال الحوامل (Holders)'),
    ('Replace spring', 'استبدال السوستة (Spring)'),
    ('Replace gas spring', 'استبدال الجاز سبرينج (Gas Spring)'),
    ('Replace cable kit', 'استبدال طقم الكابل (Cable Kit)'),
    ('Make/replace crimped-end cable', 'عمل/استبدال الكابل بطرف مكرمب (Crimped End Cable)'),
    ('Repair/replace frame parts', 'إصلاح/استبدال أجزاء الفريم (الهيكل)'),
    ('Replace platform', 'استبدال المنصة/البلاتفورم (Platform)'),
    ('Tighten nuts and bolts (monthly check)', 'شد الصواميل والمسامير (فحص شهري)'),
    ('Clean and lubricate weight-stack and transfer pulleys', 'تنظيف وتشحيم بكرات حزمة الأوزان وبكرات النقل'),
    ('Install backup safety cable', 'تركيب نظام حبل/كابل الأمان الاحتياطي (Backup Safety Cable)'),
    ('Diagnose and replace damaged transfer cables', 'تشخيص واستبدال كابلات النقل التالفة'),
]

# Cardio / supervisor level: excluded from the certification bar, marks
# readiness for the supervisor track (docs/database_design_v2.md, §3).
CARDIO_SKILLS = [
    ('Treadmill — running belt', 'تريدميل: سير الجري (Belt)'),
    ('Treadmill — deck', 'تريدميل: اللوح تحت السير (Deck)'),
    ('Treadmill — motor', 'تريدميل: الموتور'),
    ('Treadmill — motor control board / inverter (MCB)', 'تريدميل: بورد التحكم بالموتور / الإنفرتر (MCB)'),
    ('Treadmill — incline motor', 'تريدميل: موتور/محرك الميل (Incline Motor)'),
    ('Treadmill — front/rear rollers and bearings', 'تريدميل: البكرات الأمامية/الخلفية (Rollers) وجلاليدها'),
    ('Treadmill — drive belt (motor to roller)', 'تريدميل: سير نقل الحركة (من الموتور للبكرة)'),
    ('Treadmill — console/display', 'تريدميل: الشاشة/الكونسول'),
    ('Treadmill — safety key', 'تريدميل: مفتاح الأمان (Safety Key)'),
    ('Treadmill — internal wiring', 'تريدميل: الأسلاك الداخلية'),
    ('Bike — pedals', 'دراجات: البدالات (Pedals)'),
    ('Bike — crank arms', 'دراجات: أذرع الكرنك (Crank Arms)'),
    ('Bike — resistance/brake unit', 'دراجات: وحدة المقاومة المغناطيسية (Resistance/Brake Unit)'),
    ('Bike — flywheel bearing', 'دراجات: جلبة الفلايويل (Flywheel Bearing)'),
    ('Bike — drive belt/chain', 'دراجات: سير/جنزير النقل'),
    ('Bike — recumbent seat rail', 'دراجات: قضيب وكرسي الدراجة المستلقية (Seat Rail)'),
]


def reseed(apps, schema_editor):
    Skill = apps.get_model('reference', 'Skill')

    # Never delete — TechnicianSkill/TechnicianSkillAssessment rows still
    # reference these with on_delete=PROTECT, and the project's own rule
    # is never delete a record with history (docs/database_design_v2.md).
    # Deactivating drops them off every screen without touching that data.
    Skill.objects.update(is_active=False)

    for name, name_ar in BASIC_SKILLS:
        Skill.objects.create(name=name, name_ar=name_ar, category='other', is_active=True)
    for name, name_ar in CARDIO_SKILLS:
        Skill.objects.create(name=name, name_ar=name_ar, category='cardio', is_active=True)


def unseed(apps, schema_editor):
    Skill = apps.get_model('reference', 'Skill')
    new_names = [name for name, _name_ar in BASIC_SKILLS + CARDIO_SKILLS]
    Skill.objects.filter(name__in=new_names).delete()
    Skill.objects.exclude(name__in=new_names).update(is_active=True)


class Migration(migrations.Migration):

    dependencies = [
        ('reference', '0010_alter_skill_options_remove_skill_brand_skill_name_ar_and_more'),
    ]

    operations = [
        migrations.RunPython(reseed, unseed),
    ]
