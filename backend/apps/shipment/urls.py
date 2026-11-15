"""出货域路由（无尾斜杠，契约 §四；全部挂公司隔离 + 权限点校验）。"""
from django.urls import path

from apps.shipment import views

urlpatterns = [
    # 字面段在前（同 finance/cost 惯例）
    path('shipments/<int:pk>/undelivered',
         views.ShipmentUndeliveredView.as_view()),
    path('shipments/<int:pk>/print-file', views.ShipmentPrintFileView.as_view()),
    path('shipments/<int:pk>/print-logs', views.ShipmentPrintLogView.as_view()),
    path('shipments/<int:pk>/audit', views.ShipmentAuditView.as_view()),
    path('shipments/<int:pk>/void', views.ShipmentVoidView.as_view()),
    path('shipments/<int:pk>/boxes', views.PackingBoxListCreateView.as_view()),
    path('shipments/<int:pk>/boxes/<int:box_id>',
         views.PackingBoxDeleteView.as_view()),
    path('shipments/<int:pk>', views.ShipmentDetailView.as_view()),
    path('shipments', views.ShipmentListCreateView.as_view()),
]
