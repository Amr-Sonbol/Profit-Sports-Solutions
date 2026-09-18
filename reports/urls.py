from django.urls import path

from . import views

app_name = 'reports'

urlpatterns = [
    path('feedback/<str:token>/', views.feedback_form, name='feedback_form'),
]
