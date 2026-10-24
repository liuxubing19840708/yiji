"""轻量仓库与追溯路由（无尾斜杠，挂 /api/ 下）。"""
from django.urls import path

from apps.warehouse import views

urlpatterns = [
    # 面料批次
    path('lots', views.LotListCreateView.as_view()),
    path('lots/<int:pk>', views.LotDetailView.as_view()),

    # 床次 × 批次血缘（P0 手工挂批次入口；领料确认钩子也写这张表）
    path('productions/<int:production_id>/lot-links', views.ProductionLotLinkView.as_view()),

    # 追溯查询
    path('trace/lot', views.TraceLotSearchView.as_view()),
    path('trace/lot/<int:pk>', views.TraceLotDetailView.as_view()),
    path('trace/reverse', views.TraceReverseView.as_view()),
    path('trace/ticket/<int:pk>', views.TraceTicketView.as_view()),
    path('trace/production/<int:pk>', views.TraceProductionView.as_view()),

    # 采购单
    path('purchase-orders', views.PurchaseOrderListCreateView.as_view()),
    path('purchase-orders/<int:pk>', views.PurchaseOrderDetailView.as_view()),
    path('purchase-orders/<int:pk>/confirm', views.PurchaseOrderConfirmView.as_view()),

    # 入库单（批次诞生点）
    path('stock-ins', views.StockInListCreateView.as_view()),
    path('stock-ins/<int:pk>', views.StockInDetailView.as_view()),
    path('stock-ins/<int:pk>/confirm', views.StockInConfirmView.as_view()),

    # 领料单（确认钩子自动写血缘）
    path('material-issues', views.MaterialIssueListCreateView.as_view()),
    path('material-issues/<int:pk>', views.MaterialIssueDetailView.as_view()),
    path('material-issues/<int:pk>/confirm', views.MaterialIssueConfirmView.as_view()),
]
