"""供主路由 include('apps.finished_goods.urls')，路径不注册到全局配置。"""
from django.urls import path

from . import views

urlpatterns = [
    path('finished-goods/packing-tasks', views.PackingTaskListCreateView.as_view()),
    path('finished-goods/packing-tasks/<int:pk>', views.PackingTaskDetailView.as_view()),
    path('finished-goods/packing-tasks/<int:pk>/approve', views.PackingTaskApproveView.as_view()),
    path('finished-goods/packing-tasks/<int:pk>/reverse', views.PackingTaskReverseView.as_view()),
    path('finished-goods/boxes', views.PackingBoxListView.as_view()),
    path('finished-goods/boxes/<int:pk>', views.PackingBoxDetailView.as_view()),
    path('finished-goods/inbounds', views.FinishedInboundListCreateView.as_view()),
    path('finished-goods/inbounds/<int:pk>', views.FinishedInboundDetailView.as_view()),
    path('finished-goods/inbounds/<int:pk>/approve', views.FinishedInboundApproveView.as_view()),
    path('finished-goods/inbounds/<int:pk>/reverse', views.FinishedInboundReverseView.as_view()),
    path('finished-goods/inventory', views.FinishedInventoryView.as_view()),
]
