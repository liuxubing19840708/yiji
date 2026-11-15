"""应收域路由（无尾斜杠，契约 §四；全部挂公司隔离 + 权限点校验）。"""
from django.urls import path

from apps.finance import cash_views, views

urlpatterns = [
    # 批量核销（评审 Top10 #5）：字面段路由放 int 段前（同 cost 惯例）
    path('finance/customers/<int:customer_id>/receipts-batch',
         views.CustomerBatchReceiptView.as_view()),
    # receipts/revoke 须在 receipts 之前注册（字面段更稳，同 cost 惯例）
    path('finance/receivables/<int:receivable_id>/receipts/revoke',
         views.ReceiptRevokeView.as_view()),
    path('finance/receivables/<int:receivable_id>/receipts',
         views.ReceiptCreateView.as_view()),
    path('finance/receivables/<int:receivable_id>/receipt-lines',
         views.ReceiptLinesView.as_view()),
    path('finance/receivables/<int:receivable_id>/void',
         views.ReceivableVoidView.as_view()),
    path('finance/receivables/<int:receivable_id>',
         views.ReceivableDetailView.as_view()),
    path('finance/receivables', views.ReceivableView.as_view()),
    path('finance/stock-valuation', views.StockValuationView.as_view()),
    path('finance/cash-flow', cash_views.CashFlowView.as_view()),
    path('finance/payables', cash_views.PayableListCreateView.as_view()),
    path('finance/payables/<int:pk>/void', cash_views.PayableVoidView.as_view()),
    path('finance/payables/<int:pk>', cash_views.PayableDetailView.as_view()),
    path('finance/supplier-payments', cash_views.SupplierPaymentListCreateView.as_view()),
    path('finance/supplier-payments/<int:pk>/void', cash_views.SupplierPaymentVoidView.as_view()),
    path('finance/cash-entries', cash_views.CashEntryListCreateView.as_view()),
    path('finance/cash-entries/<int:pk>/void', cash_views.CashEntryVoidView.as_view()),
]
