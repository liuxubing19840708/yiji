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

    # P1 超额领料差异报表（定额 vs 实领 + 超领行 + 无定额行 + 待补参数清单）
    path('issues/divergence', views.IssueDivergenceView.as_view()),
]

# ============ P2 采购补全域（SA-P2-1，2026-10-24） ============
urlpatterns += [
    # 供应商主档（权限点 warehouse.supplier，legacy 默认零授权）
    path('suppliers', views.SupplierListCreateView.as_view()),
    path('suppliers/<int:pk>', views.SupplierDetailView.as_view()),

    # 到料暂收（暂收 → IQC 行级拆量 → 确认生成采购入库）
    path('receipts', views.ReceiptListCreateView.as_view()),
    path('receipts/<int:pk>', views.ReceiptDetailView.as_view()),
    path('receipts/<int:pk>/confirm', views.ReceiptConfirmView.as_view()),

    # 采购需求汇总（视图计算不落库，口径见 DemandSummaryView docstring）
    path('demand-summary', views.DemandSummaryView.as_view()),

    # D1 期初库存 Excel 导入（上线落地方案 §11-D1，挂 warehouse.manage）
    path('opening-stock/import', views.OpeningStockImportView.as_view()),
]
