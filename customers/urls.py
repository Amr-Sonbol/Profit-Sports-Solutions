from django.urls import path

from . import views

app_name = 'customers'

urlpatterns = [
    path('', views.customer_list, name='customer_list'),
    path('new/', views.customer_create, name='customer_create'),
    path('import/', views.customer_import, name='customer_import'),
    path('import/template/', views.customer_import_template, name='customer_import_template'),
    path('<int:pk>/', views.customer_detail, name='customer_detail'),
    path('<int:pk>/edit/', views.customer_edit, name='customer_edit'),
    path('sites/<int:pk>/edit/', views.site_edit, name='site_edit'),
    path('portal/login/', views.portal_login, name='portal_login'),
    path('portal/', views.portal_home, name='portal_home'),
    path('portal/tickets/new/', views.portal_ticket_new, name='portal_ticket_new'),
]
