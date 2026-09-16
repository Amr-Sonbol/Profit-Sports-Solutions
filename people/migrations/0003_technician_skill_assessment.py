import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reference', '0004_skill_category'),
        ('people', '0002_technician_is_available_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='technicianskill',
            name='source',
            field=models.CharField(choices=[('self', 'Self-rated'), ('supervisor', 'Supervisor')], default='supervisor', help_text='a self-rating is a starting guess — only a supervisor review counts toward certification', max_length=10, verbose_name='source'),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='technicianskill',
            name='set_by',
            field=models.ForeignKey(help_text='the technician himself for a self-rating, the supervisor for a review', on_delete=django.db.models.deletion.PROTECT, related_name='levels_set', to='people.technician', verbose_name='set by'),
        ),
        migrations.CreateModel(
            name='TechnicianSkillAssessment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('level', models.PositiveSmallIntegerField(choices=[(1, '1'), (2, '2'), (3, '3'), (4, '4')], verbose_name='level')),
                ('source', models.CharField(choices=[('self', 'Self-rated'), ('supervisor', 'Supervisor')], max_length=10, verbose_name='source')),
                ('set_on', models.DateField(verbose_name='set on')),
                ('note', models.CharField(blank=True, max_length=255, verbose_name='note')),
                ('set_by', models.ForeignKey(help_text='the technician himself for a self-rating, the supervisor for a review', on_delete=django.db.models.deletion.PROTECT, related_name='skill_assessments_made', to='people.technician', verbose_name='set by')),
                ('skill', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='technician_assessments', to='reference.skill', verbose_name='skill')),
                ('technician', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='skill_assessments', to='people.technician', verbose_name='technician')),
            ],
            options={
                'verbose_name': 'technician skill assessment',
                'verbose_name_plural': 'technician skill assessments',
                'ordering': ['-set_on', '-id'],
            },
        ),
    ]
