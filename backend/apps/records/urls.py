"""扫菲登记路由（无尾斜杠）。"""
from django.urls import path

from apps.records import views

urlpatterns = [
    path('records', views.RecordListCreateView.as_view()),
    path('records/<int:pk>', views.RecordDetailView.as_view()),
    path('records/process-selector', views.ProcessSelectorView.as_view()),
    path('tickets/lookup', views.TicketLookupView.as_view()),
    path('records/bundle-progress', views.BundleProgressView.as_view()),
    path('records/workbench', views.WorkbenchView.as_view()),
    # 竞品对齐（2026-11-04 GAP-2/GAP-3/GAP-7）
    path('records/batch-register', views.BatchRegisterView.as_view()),
    path('records/batch-undo', views.BatchUndoView.as_view()),
    path('records/export', views.RecordExportView.as_view()),
]
