from django.urls import path

from apps.ops import views

urlpatterns = [
    path('settings', views.SettingView.as_view()),
    path('audit-logs', views.AuditLogListView.as_view()),
    path('notifications', views.NotificationListView.as_view()),
    path('notifications/read-all', views.NotificationReadView.as_view()),
    path('notifications/<int:pk>/read', views.NotificationReadView.as_view()),
]
