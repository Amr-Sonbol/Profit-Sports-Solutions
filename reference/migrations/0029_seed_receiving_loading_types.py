from django.db import migrations

TYPES = [
    ('receiving_from_factory', 'Receiving from the Factory', 'الاستلام من المصنع', 'receiving'),
    ('loading', 'Loading', 'التحميل', 'loading'),
]


def add_types(apps, schema_editor):
    TaskType = apps.get_model('reference', 'TaskType')
    for code, name, name_ar, category in TYPES:
        TaskType.objects.get_or_create(code=code, defaults={'name': name, 'name_ar': name_ar, 'category': category})


def remove_types(apps, schema_editor):
    TaskType = apps.get_model('reference', 'TaskType')
    TaskType.objects.filter(code__in=[code for code, *_rest in TYPES], tasks__isnull=True).delete()


class Migration(migrations.Migration):
    """The equipment chain's first two steps as task types (docs: task_product)."""

    dependencies = [('reference', '0028_task_type_receiving_loading')]
    operations = [migrations.RunPython(add_types, remove_types)]
