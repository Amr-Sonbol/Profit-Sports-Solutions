from django.contrib import admin

from .models import (
    RolePermission, Technician, TechnicianConduct, TechnicianConductAssessment, TechnicianSkill,
    TechnicianSkillAssessment,
)


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    """Also editable from the in-app Roles & permissions screen — this is
    the same data, just reachable from /admin/ too.
    """
    list_display = ['permission', 'role', 'allowed']
    list_filter = ['permission', 'role', 'allowed']
    list_editable = ['allowed']


@admin.register(Technician)
class TechnicianAdmin(admin.ModelAdmin):
    list_display = [
        'full_name', 'country', 'role', 'employment_type', 'language', 'is_active',
        'is_available', 'unavailable_reason',
    ]
    search_fields = ['full_name', 'phone']
    list_filter = ['country', 'role', 'employment_type', 'language', 'is_active', 'is_available']


@admin.register(TechnicianSkill)
class TechnicianSkillAdmin(admin.ModelAdmin):
    list_display = ['technician', 'skill', 'level', 'source', 'set_by', 'set_on']
    search_fields = ['technician__full_name', 'skill__name', 'skill__brand__name']
    list_filter = ['skill__brand', 'level', 'source']


@admin.register(TechnicianSkillAssessment)
class TechnicianSkillAssessmentAdmin(admin.ModelAdmin):
    list_display = ['technician', 'skill', 'level', 'source', 'set_by', 'set_on']
    search_fields = ['technician__full_name', 'skill__name', 'skill__brand__name']
    list_filter = ['skill__brand', 'level', 'source']


@admin.register(TechnicianConduct)
class TechnicianConductAdmin(admin.ModelAdmin):
    list_display = ['technician', 'conduct_area', 'level', 'source', 'set_by', 'set_on']
    search_fields = ['technician__full_name', 'conduct_area__name']
    list_filter = ['conduct_area', 'level', 'source']


@admin.register(TechnicianConductAssessment)
class TechnicianConductAssessmentAdmin(admin.ModelAdmin):
    list_display = ['technician', 'conduct_area', 'level', 'source', 'set_by', 'set_on']
    search_fields = ['technician__full_name', 'conduct_area__name']
    list_filter = ['conduct_area', 'level', 'source']
