"""生产管理路由（无尾斜杠）。"""
from django.urls import path

from apps.production import views

urlpatterns = [
    path('productions', views.ProductionListCreateView.as_view()),
    path('productions/<int:pk>', views.ProductionDetailView.as_view()),
    path('productions/<int:pk>/lock', views.ProductionLockView.as_view()),
    path('productions/<int:pk>/cut-done', views.ProductionCutDoneView.as_view()),
    path('productions/<int:pk>/order-processes', views.ProductionOrderProcessView.as_view()),
    path('productions/<int:pk>/bundles', views.ProductionBundleListView.as_view()),
    path('productions/<int:pk>/cut-detail', views.ProductionCutDetailView.as_view()),

    path('production-colors', views.ProductionColorStatsView.as_view()),
    path('production-hints', views.ProductionInputHintsView.as_view()),

    path('dashboard', views.DashboardView.as_view()),
    path('dashboard/dim', views.DashboardDimView.as_view()),

    path('scan/parse', views.ScanParseView.as_view()),
]
