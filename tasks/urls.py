from django.urls import path

from . import views

app_name = 'tasks'

urlpatterns = [
    path('', views.task_list, name='task_list'),
    path('week/', views.task_week, name='task_week'),
    path('my-week/', views.my_week, name='my_week'),
    path('my/<int:pk>/', views.my_task_detail, name='my_task_detail'),
    path('new/', views.task_create, name='task_create'),
    path('<int:pk>/', views.task_detail, name='task_detail'),
    path('<int:pk>/assign/', views.task_assign, name='task_assign'),
]
