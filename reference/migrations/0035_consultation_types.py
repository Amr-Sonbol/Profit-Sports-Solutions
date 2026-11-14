"""Site consultation and online meeting are consultations, not installations
(docs/hardening_plan.md 3.7, owner 13 Nov 2026): no "site not ready" warnings, no
machine plans, not on the warehouse's list. Relocation stays an installation."""
from django.db import migrations


def to_consultation(apps, schema_editor):
    apps.get_model('reference', 'TaskType').objects.filter(
        code__in=['site_consultation_assessment', 'online_meeting'],
    ).update(category='consultation')


def back(apps, schema_editor):
    apps.get_model('reference', 'TaskType').objects.filter(
        code__in=['site_consultation_assessment', 'online_meeting'],
    ).update(category='installation')


class Migration(migrations.Migration):
    dependencies = [('reference', '0034_tasktype_category_consultation')]
    operations = [migrations.RunPython(to_consultation, back)]
