"""核心模块路由：通用上传、客户端日志。"""
from django.urls import path

from . import views

urlpatterns = [
    path('uploads', views.UploadView.as_view()),
    path('uploads/video', views.VideoUploadView.as_view()),
    path('permissions', views.PermissionDictView.as_view()),
    path('client-logs', views.ClientLogView.as_view()),
]
