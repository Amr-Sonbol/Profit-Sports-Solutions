from django.db import migrations

COUNTRIES = [
    # name, name_ar, iso_code, timezone, currency_code
    ('Egypt', 'مصر', 'EG', 'Africa/Cairo', 'EGP'),
    ('Bahrain', 'البحرين', 'BH', 'Asia/Bahrain', 'BHD'),
    ('Qatar', 'قطر', 'QA', 'Asia/Qatar', 'QAR'),
    ('United Arab Emirates', 'الإمارات العربية المتحدة', 'AE', 'Asia/Dubai', 'AED'),
    ('Saudi Arabia', 'المملكة العربية السعودية', 'SA', 'Asia/Riyadh', 'SAR'),
    ('Oman', 'عُمان', 'OM', 'Asia/Muscat', 'OMR'),
    ('United States', 'الولايات المتحدة الأمريكية', 'US', 'America/New_York', 'USD'),
    ('Canada', 'كندا', 'CA', 'America/Toronto', 'CAD'),
    ('Kuwait', 'الكويت', 'KW', 'Asia/Kuwait', 'KWD'),
]

BRANDS = [
    'Panatta',
    'Xenios USA',
    'K-Well',
    'Kraiburg',
    'Ciclotte',
    'Eleiko',
    'Fit Interiors',
    'The ABS Company',
    'Total Gym',
    'Marpo',
    'Street Barbell',
    'Mondo',
    'Soinca',
    'Digilock',
    'Safina',
    'Hatko',
    'Polytan',
    'Orange Padel International',
]

# (code, name, name_ar, category)
TASK_TYPES = [
    ('preventive_maintenance', 'Preventive Maintenance', 'صيانة وقائية', 'maintenance'),
    ('breakdown_repair', 'Breakdown Repair', 'إصلاح عطل', 'maintenance'),
    ('new_installation', 'New Installation', 'تركيب جديد', 'installation'),
    ('relocation_reinstallation', 'Relocation / Reinstallation', 'نقل - إعادة تركيب', 'installation'),
    ('safety_inspection', 'Safety Inspection', 'فحص السلامة', 'maintenance'),
    ('spare_parts_replacement', 'Spare Parts Replacement', 'استبدال قطع الغيار', 'maintenance'),
    ('upholstery_repair', 'Upholstery Repair', 'إصلاح التنجيد', 'maintenance'),
    ('calibration_adjustment', 'Calibration / Adjustment', 'معايرة - ضبط', 'maintenance'),
    ('deep_cleaning_servicing', 'Deep Cleaning / Servicing', 'تنظيف عميق - خدمة', 'maintenance'),
    ('warranty_claim_service', 'Warranty Claim Service', 'خدمة مطالبة الضمان', 'maintenance'),
    ('emergency_callout', 'Emergency Callout', 'بلاغ طارئ', 'maintenance'),
    ('site_consultation_assessment', 'Site Consultation / Assessment', 'استشارة - معاينة الموقع', 'installation'),
]


def seed(apps, schema_editor):
    Country = apps.get_model('reference', 'Country')
    Brand = apps.get_model('reference', 'Brand')
    Skill = apps.get_model('reference', 'Skill')
    TaskType = apps.get_model('reference', 'TaskType')

    for name, name_ar, iso_code, timezone, currency_code in COUNTRIES:
        Country.objects.create(
            name=name, name_ar=name_ar, iso_code=iso_code,
            timezone=timezone, currency_code=currency_code,
        )

    for name in BRANDS:
        brand = Brand.objects.create(name=name)
        Skill.objects.create(brand=brand, name=brand.name)

    for code, name, name_ar, category in TASK_TYPES:
        TaskType.objects.create(
            code=code, name=name, name_ar=name_ar, category=category,
        )


def unseed(apps, schema_editor):
    apps.get_model('reference', 'TaskType').objects.filter(
        code__in=[code for code, *_ in TASK_TYPES],
    ).delete()
    apps.get_model('reference', 'Skill').objects.filter(
        brand__name__in=BRANDS,
    ).delete()
    apps.get_model('reference', 'Brand').objects.filter(name__in=BRANDS).delete()
    apps.get_model('reference', 'Country').objects.filter(
        iso_code__in=[row[2] for row in COUNTRIES],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('reference', '0002_country_name_ar'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
