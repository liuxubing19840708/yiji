"""报价域路由（无尾斜杠，契约 §四；全部挂公司隔离 + 权限点校验）。"""
from django.urls import path

from apps.quotation import views

urlpatterns = [
    path('quotation/quotations', views.QuotationListCreateView.as_view()),
    path('quotation/quotations/<int:pk>', views.QuotationDetailView.as_view()),
    path('quotation/quotations/<int:pk>/mark-status',
         views.QuotationMarkStatusView.as_view()),
    path('quotation/quotations/<int:pk>/backfill-compare',
         views.QuotationBackfillCompareView.as_view()),
]
