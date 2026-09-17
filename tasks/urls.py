from django.urls import path

from . import views

app_name = 'tasks'

urlpatterns = [
    path('', views.task_list, name='task_list'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('week/', views.task_week, name='task_week'),
    path('my-week/', views.my_week, name='my_week'),
    path('my-progress/', views.my_progress, name='my_progress'),
    path('my-skills/', views.my_skills, name='my_skills'),
    path('my/<int:pk>/', views.my_task_detail, name='my_task_detail'),
    path('my/<int:pk>/report/', views.my_report_form, name='my_report_form'),
    path('new/', views.task_create, name='task_create'),
    path('tickets/new/', views.ticket_form, name='ticket_form'),
    path('tickets/new/thank-you/', views.ticket_submitted, name='ticket_submitted'),
    path('tickets/', views.ticket_list, name='ticket_list'),
    path('tickets/<int:pk>/', views.ticket_review, name='ticket_review'),
    path('technicians/', views.technician_list, name='technician_list'),
    path('technicians/<int:pk>/board/', views.technician_board, name='technician_board'),
    path('technicians/<int:pk>/skills/', views.technician_skills, name='technician_skills'),
    path('roles/', views.role_permissions, name='role_permissions'),
    path('<int:pk>/', views.task_detail, name='task_detail'),
    path('<int:pk>/assign/', views.task_assign, name='task_assign'),
]
