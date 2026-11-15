"""薪酬结算路由（无尾斜杠）。"""
from django.urls import path

from apps.salary import views

urlpatterns = [
    path('salary/company', views.CompanySummaryView.as_view()),
    path('salary/company/employees',
         views.CompanyEmployeesView.as_view()),
    path('salary/employee/<int:uid>', views.EmployeeSalaryView.as_view()),
    path('salary/employee/<int:uid>/config',
         views.EmployeeConfigView.as_view()),
    path('salary/adjust', views.AdjustListCreateView.as_view()),
    path('salary/payments', views.SalaryPaymentListCreateView.as_view()),
    path('salary/payments/<int:pk>', views.SalaryPaymentDetailView.as_view()),
    path('salary/payments/<int:pk>/void', views.SalaryPaymentVoidView.as_view()),
    path('salary/settlements', views.SettlementListCreateView.as_view()),
    path('salary/settlements/<int:pk>/payslip',
         views.SettlementPayslipView.as_view()),  # 竞品对齐 GAP-4（2026-11-04）
    path('salary/settlements/<int:pk>/payslip/export',
         views.SettlementPayslipExportView.as_view()),
    path('salary/settlements/<int:pk>/revoke',
         views.SettlementRevokeView.as_view()),
    path('salary/settle-applies', views.SettleApplyListCreateView.as_view()),
    path('salary/settle-applies/<int:pk>/review',
         views.SettleApplyReviewView.as_view()),
    path('salary/settle-applies/<int:pk>/pay',
         views.SettleApplyPayView.as_view()),
    path('salary/settle-applies/<int:pk>/void',
         views.SettleApplyVoidView.as_view()),
    path('salary/summary/completion', views.SummaryCompletionView.as_view()),
]
