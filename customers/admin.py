from django.contrib import admin

from .models import Asset, Customer, PortalUser, Site


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'country', 'segment', 'contact_name', 'contact_phone', 'contact_email', 'is_active']
    search_fields = ['name', 'code', 'contact_name', 'contact_email', 'portal_users__user__username']
    list_filter = ['country', 'segment', 'is_active']


@admin.register(PortalUser)
class PortalUserAdmin(admin.ModelAdmin):
    list_display = ['user', 'customer', 'must_change_password', 'invited_at']
    search_fields = ['user__username', 'user__first_name', 'customer__name']


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ['name', 'customer', 'contact_name', 'contact_phone', 'contact_email']
    search_fields = ['name', 'customer__name', 'contact_name', 'contact_email']
    list_filter = ['customer']


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ['site', 'brand', 'model_name', 'serial_no', 'status']
    search_fields = ['model_name', 'serial_no', 'site__name']
    list_filter = ['brand', 'status']
