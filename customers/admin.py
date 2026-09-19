from django.contrib import admin

from .models import Asset, Customer, Site


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['name', 'country', 'segment', 'contact_name', 'contact_phone', 'contact_email', 'is_active']
    search_fields = ['name', 'contact_name', 'contact_email']
    list_filter = ['country', 'segment', 'is_active']


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
