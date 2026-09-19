from django.test import TestCase
from django.utils import translation

from .models import Country, Skill, TaskType


class SeedReferenceDataTests(TestCase):
    """Locks in the data migration in 0003_seed_reference_data.py."""

    def test_countries_have_arabic_names(self):
        egypt = Country.objects.get(iso_code='EG')
        self.assertEqual(egypt.name_ar, 'مصر')
        self.assertEqual(Country.objects.count(), 9)

    def test_skills_are_brand_agnostic_repair_tasks(self):
        # 0003/0005 originally seeded 21 brand-based skills (18 brands +
        # Panatta/Skillcore cardio lines) — 0011 deactivates all of those
        # and reseeds 18 basic + 16 cardio repair-task skills in their
        # place; 0012 adds 4 more cardio skills for elliptical machines.
        # The old, now-inactive rows are kept, never deleted (see
        # docs/database_design_v2.md rule 4), so the table holds both.
        # 0014/0015 later hard-delete 3 of the reseeded basic skills
        # (backup safety cable, cable kit, crimped-end cable) — they had
        # no rating/assessment/task history, so nothing to preserve.
        self.assertEqual(Skill.objects.filter(is_active=True, category=Skill.Category.OTHER).count(), 15)
        self.assertEqual(Skill.objects.filter(is_active=True, category=Skill.Category.CARDIO).count(), 20)
        self.assertEqual(Skill.objects.filter(is_active=False).count(), 21)
        self.assertTrue(Skill.objects.get(is_active=True, name='Replace pins'))
        self.assertTrue(Skill.objects.get(is_active=True, name='Treadmill — running belt'))
        self.assertTrue(Skill.objects.get(is_active=True, name='Elliptical — check transmission belt'))

    def test_skill_display_name_follows_active_language(self):
        pins = Skill.objects.get(is_active=True, name='Replace pins')
        with translation.override('en'):
            self.assertEqual(pins.display_name, 'Replace pins')
            self.assertEqual(str(pins), 'Replace pins')
        with translation.override('ar'):
            self.assertEqual(pins.display_name, 'استبدال البنّات (Pins)')
            self.assertEqual(str(pins), 'استبدال البنّات (Pins)')

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
