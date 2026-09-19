from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import (
    NotificationSettings, RolePermission, Technician, TechnicianConduct, TechnicianConductAssessment,
    TechnicianSkill, TechnicianSkillAssessment,
)

User = get_user_model()


def _cascade_active(queryset, is_active, related_attr):
    """For User querysets — Technician has its own set_active() (used by
    the actions below and the in-app roster deactivate screen) since it
    also has a reason to track; a User has no such reason to carry over.
    """
    for obj in queryset:
        obj.is_active = is_active
        obj.save(update_fields=['is_active'])
        related = getattr(obj, related_attr, None)
        if related is not None:
            related.is_active = is_active
            related.save(update_fields=['is_active'])


@admin.action(description='Suspend selected technicians (also blocks their login)')
def suspend_technicians(modeladmin, request, queryset):
    for technician in queryset:
        technician.set_active(False)


@admin.action(description='Reactivate selected technicians (also restores their login)')
def reactivate_technicians(modeladmin, request, queryset):
    for technician in queryset:
        technician.set_active(True)


@admin.action(description="Suspend selected users (also marks their technician profile inactive)")
def suspend_users(modeladmin, request, queryset):
    _cascade_active(queryset, False, 'technician')


@admin.action(description="Reactivate selected users (also marks their technician profile active)")
def reactivate_users(modeladmin, request, queryset):
    _cascade_active(queryset, True, 'technician')


admin.site.unregister(User)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = DjangoUserAdmin.list_display + ('is_active', 'linked_technician')
    list_editable = ('is_active',)
    actions = [suspend_users, reactivate_users]

    def linked_technician(self, obj):
        technician = getattr(obj, 'technician', None)
        return technician.full_name if technician else '—'
    linked_technician.short_description = 'Technician'


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    """Also editable from the in-app Roles & permissions screen — this is
    the same data, just reachable from /admin/ too.
    """
    list_display = ['permission', 'role', 'allowed']
    list_filter = ['permission', 'role', 'allowed']
    list_editable = ['allowed']


@admin.register(NotificationSettings)
class NotificationSettingsAdmin(admin.ModelAdmin):
    """One row. Also editable from the in-app Roles & permissions screen."""
    list_display = ['auto_notify_on_reschedule']

    def has_add_permission(self, request):
        return not NotificationSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Technician)
class TechnicianAdmin(admin.ModelAdmin):
    list_display = [
        'full_name', 'country', 'role', 'employment_type', 'language', 'is_active',
        'is_available', 'unavailable_reason',
    ]
    search_fields = ['full_name', 'phone']
    list_filter = ['country', 'role', 'employment_type', 'language', 'is_active', 'is_available']
    list_editable = ['is_active', 'is_available']
    actions = [suspend_technicians, reactivate_technicians]

    def save_model(self, request, obj, form, change):
        if change and 'is_active' in form.changed_data:
            obj.set_active(obj.is_active, reason=obj.deactivation_reason)
        else:
            super().save_model(request, obj, form, change)


@admin.register(TechnicianSkill)
class TechnicianSkillAdmin(admin.ModelAdmin):
    list_display = ['technician', 'skill', 'level', 'source', 'set_by', 'set_on']
    search_fields = ['technician__full_name', 'skill__name']
    list_filter = ['skill__category', 'level', 'source']


@admin.register(TechnicianSkillAssessment)
class TechnicianSkillAssessmentAdmin(admin.ModelAdmin):
    list_display = ['technician', 'skill', 'level', 'source', 'set_by', 'set_on']
    search_fields = ['technician__full_name', 'skill__name']
    list_filter = ['skill__category', 'level', 'source']


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
