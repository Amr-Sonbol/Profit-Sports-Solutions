from django.contrib import admin

from .models import Asset, Customer, Site


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['name', 'country', 'segment', 'is_active']
    search_fields = ['name']
    list_filter = ['country', 'segment', 'is_active']


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ['name', 'customer', 'contact_name', 'contact_phone']
    search_fields = ['name', 'customer__name', 'contact_name']
    list_filter = ['customer']


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ['site', 'brand', 'model_name', 'serial_no', 'status']
    search_fields = ['model_name', 'serial_no', 'site__name']
    list_filter = ['brand', 'status']
