from django.db import migrations

# The old single request type, as (about, need).
TOPIC_TO_ABOUT_NEED = {
    'machine_problem': ('machines', 'repair'),
    'flooring': ('flooring', 'repair'),
    'accessories': ('accessories', 'repair'),
    'visit': ('machines', 'visit'),
    'parts': ('machines', 'parts'),
    'assistance': ('other', 'assistance'),
    'other': ('other', 'assistance'),
}


def forwards(apps, schema_editor):
    CustomerTicket = apps.get_model('tasks', 'CustomerTicket')
    for topic, (about, need) in TOPIC_TO_ABOUT_NEED.items():
        CustomerTicket.objects.filter(topic=topic).update(about=about, need=need)


def backwards(apps, schema_editor):
    CustomerTicket = apps.get_model('tasks', 'CustomerTicket')
    for topic, (about, need) in TOPIC_TO_ABOUT_NEED.items():
        CustomerTicket.objects.filter(about=about, need=need).update(topic=topic)


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0070_customerticket_about_need'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
