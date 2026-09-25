from django.urls import path

from . import views

app_name = 'api'

urlpatterns = [
    path('login/', views.LoginView.as_view(), name='login'),
    path('me/', views.MeView.as_view(), name='me'),
    path('my-tasks/', views.MyTaskListView.as_view(), name='my_task_list'),
    path('my-tasks/<int:pk>/', views.MyTaskDetailView.as_view(), name='my_task_detail'),
    path('my-tasks/<int:pk>/action/', views.MyTaskActionView.as_view(), name='my_task_action'),
    path('my-tasks/<int:pk>/attachments/', views.MyTaskAttachmentView.as_view(), name='my_task_attachment'),
    path('tasks/', views.TaskListView.as_view(), name='task_list'),
    path('tickets/', views.TicketListView.as_view(), name='ticket_list'),
]
