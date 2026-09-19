from django.contrib import admin

from .models import Brand, ConductArea, Country, Skill, TaskType


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ['name', 'name_ar', 'iso_code', 'task_prefix', 'timezone', 'currency_code', 'is_active']
    search_fields = ['name', 'name_ar', 'iso_code', 'task_prefix']
    list_filter = ['is_active']


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ['name', 'portal_url', 'is_active']
    search_fields = ['name']
    list_filter = ['is_active']


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ['name', 'name_ar', 'category', 'is_active']
    search_fields = ['name', 'name_ar']
    list_filter = ['category', 'is_active']


@admin.register(ConductArea)
class ConductAreaAdmin(admin.ModelAdmin):
    list_display = ['name', 'name_ar', 'is_active']
    search_fields = ['name', 'name_ar']
    list_filter = ['is_active']


@admin.register(TaskType)
class TaskTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'name_ar', 'code', 'category', 'is_active']
    search_fields = ['name', 'name_ar', 'code']
    list_filter = ['category', 'is_active']
