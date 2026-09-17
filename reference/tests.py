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
        # 18 seeded brands, each with one "other" skill, plus Skillcore
        # (added by 0005_cardio_lines, since it — like Panatta — needs a
        # separate cardio line) and the two brands' Cardio skills.
        self.assertEqual(Brand.objects.count(), 19)
        self.assertEqual(Skill.objects.count(), 21)
        panatta = Brand.objects.get(name='Panatta')
        self.assertEqual(
            Skill.objects.get(brand=panatta, category=Skill.Category.OTHER).name, 'Panatta',
        )

    def test_panatta_and_skillcore_have_a_cardio_line(self):
        for brand_name in ['Panatta', 'Skillcore']:
            brand = Brand.objects.get(name=brand_name)
            cardio = Skill.objects.get(brand=brand, category=Skill.Category.CARDIO)
            self.assertEqual(cardio.name, 'Cardio')

    def test_every_seeded_country_has_its_own_task_prefix(self):
        # The company doesn't use ISO codes at all — every country needs
        # its own local abbreviation, not just the ones that differ from
        # their ISO code.
        expected = {
            'EG': 'EGY', 'BH': 'BAH', 'QA': 'QAT', 'AE': 'UAE', 'SA': 'KSA',
            'OM': 'OMN', 'US': 'USA', 'CA': 'CAN', 'KW': 'KWT',
        }
        actual = dict(Country.objects.values_list('iso_code', 'task_prefix'))
        self.assertEqual(actual, expected)

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
