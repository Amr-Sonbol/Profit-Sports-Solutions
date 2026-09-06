from django.test import TestCase
from django.utils import translation

from .models import Brand, Country, Skill, TaskType


class SeedReferenceDataTests(TestCase):
    """Locks in the data migration in 0003_seed_reference_data.py."""

    def test_countries_have_arabic_names(self):
        egypt = Country.objects.get(iso_code='EG')
        self.assertEqual(egypt.name_ar, 'مصر')
        self.assertEqual(Country.objects.count(), 9)

    def test_one_skill_per_brand(self):
        self.assertEqual(Brand.objects.count(), 18)
        self.assertEqual(Skill.objects.count(), 18)
        panatta = Brand.objects.get(name='Panatta')
        self.assertEqual(Skill.objects.get(brand=panatta).name, 'Panatta')

    def test_task_types_have_bilingual_names_and_stable_codes(self):
        installation = TaskType.objects.get(code='new_installation')
        self.assertEqual(installation.name, 'New Installation')
        self.assertEqual(installation.name_ar, 'تركيب جديد')
        self.assertEqual(installation.category, TaskType.Category.INSTALLATION)
        self.assertEqual(
            TaskType.objects.filter(
                code__in=[
                    'preventive_maintenance', 'breakdown_repair', 'new_installation',
                    'relocation_reinstallation', 'safety_inspection',
                    'spare_parts_replacement', 'upholstery_repair',
                    'calibration_adjustment', 'deep_cleaning_servicing',
                    'warranty_claim_service', 'emergency_callout',
                    'site_consultation_assessment',
                ],
            ).count(),
            12,
        )

    def test_task_type_display_name_follows_active_language(self):
        installation = TaskType.objects.get(code='new_installation')
        with translation.override('en'):
            self.assertEqual(installation.display_name, 'New Installation')
            self.assertEqual(str(installation), 'New Installation')
        with translation.override('ar'):
            self.assertEqual(installation.display_name, 'تركيب جديد')
            self.assertEqual(str(installation), 'تركيب جديد')
