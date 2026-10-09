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
    path('my-tasks/<int:pk>/report/', views.MyTaskReportView.as_view(), name='my_task_report'),
    path('tasks/', views.TaskListView.as_view(), name='task_list'),
    path('tasks/<int:pk>/', views.TeamTaskDetailView.as_view(), name='team_task_detail'),
    path('tasks/<int:pk>/candidates/', views.TeamTaskCandidatesView.as_view(), name='team_task_candidates'),
    path('tasks/<int:pk>/assign/', views.TeamTaskAssignView.as_view(), name='team_task_assign'),
    path('tasks/<int:pk>/approve/', views.TeamTaskApproveView.as_view(), name='team_task_approve'),
    path('parts/', views.PartListView.as_view(), name='part_list'),
    path('push-device/', views.PushDeviceView.as_view(), name='push_device'),
    path('tickets/', views.TicketListView.as_view(), name='ticket_list'),
]
