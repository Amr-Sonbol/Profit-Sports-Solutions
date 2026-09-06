from django.contrib import admin

from .models import Technician, TechnicianSkill


@admin.register(Technician)
class TechnicianAdmin(admin.ModelAdmin):
    list_display = [
        'full_name', 'country', 'role', 'employment_type', 'language', 'is_active',
    ]
    search_fields = ['full_name', 'phone']
    list_filter = ['country', 'role', 'employment_type', 'language', 'is_active']


@admin.register(TechnicianSkill)
class TechnicianSkillAdmin(admin.ModelAdmin):
    list_display = ['technician', 'skill', 'level', 'set_by', 'set_on']
    search_fields = ['technician__full_name', 'skill__name', 'skill__brand__name']
    list_filter = ['skill__brand', 'level']
