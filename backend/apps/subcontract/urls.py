"""外发路由（无尾斜杠）。"""
from django.urls import path

from apps.subcontract import views

urlpatterns = [
    path('outsource/orders', views.OutsourceOrderListCreateView.as_view()),
    path('outsource/orders/<int:pk>', views.OutsourceOrderDetailView.as_view()),
    path('outsource/export', views.OutsourceExportView.as_view()),
]
