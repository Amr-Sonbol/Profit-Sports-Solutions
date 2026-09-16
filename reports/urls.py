from django.urls import path

from . import views

app_name = 'reports'

urlpatterns = [
    path('', views.report_list, name='report_list'),
    path('feedback/<str:token>/', views.feedback_form, name='feedback_form'),
    path('<int:pk>/', views.report_review, name='report_review'),
]
