"""质检路由（无尾斜杠）。"""
from django.urls import path

from apps.quality import views

urlpatterns = [
    path('quality/records', views.QualityRecordListCreateView.as_view()),
    path('quality/records/<int:pk>', views.QualityRecordDetailView.as_view()),
    path('quality/export', views.QualityExportView.as_view()),
    path('quality/inspections', views.QualityInspectionListCreateView.as_view()),
    path('quality/inspections/<int:pk>', views.QualityInspectionDetailView.as_view()),
    path('quality/inspections/<int:pk>/confirm', views.QualityInspectionConfirmView.as_view()),
]
