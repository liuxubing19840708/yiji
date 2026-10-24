"""订单管理路由（无尾斜杠，契约 §四）。"""
from django.urls import path

from apps.orders import views

urlpatterns = [
    path('orders', views.OrderListCreateView.as_view()),
    path('orders/<int:pk>', views.OrderDetailView.as_view()),
    path('orders/<int:pk>/status', views.OrderStatusView.as_view()),
    path('orders/<int:pk>/progress', views.OrderProgressView.as_view()),
]
