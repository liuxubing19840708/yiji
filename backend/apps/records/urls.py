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
]
