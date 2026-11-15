"""统一生产节点 API。"""
from django.urls import path

from . import views

urlpatterns = [
    path('manufacturing/stages', views.StageListCreateView.as_view()),
    path('manufacturing/stages/<int:pk>', views.StageDetailView.as_view()),
    path('manufacturing/stages/<int:pk>/status', views.StageStatusView.as_view()),
    path('manufacturing/routes', views.RouteListCreateView.as_view()),
    path('manufacturing/operations', views.OperationsDashboardView.as_view()),
    path('manufacturing/routes/<int:pk>', views.RouteDetailView.as_view()),
    path('manufacturing/routes/<int:pk>/status', views.RouteStatusView.as_view()),
    path('manufacturing/routes/<int:pk>/copy', views.RouteCopyView.as_view()),
    path('manufacturing/routes/<int:pk>/new-version', views.RouteNewVersionView.as_view()),
    path('manufacturing/routes/<int:pk>/instantiate', views.RouteInstantiateView.as_view()),
    path('manufacturing/routes/<int:pk>/balances', views.RouteBalancesView.as_view()),
    path('manufacturing/routes/<int:pk>/timeline', views.RouteTimelineView.as_view()),
    path('manufacturing/routes/<int:pk>/dashboard', views.RouteDashboardView.as_view()),
    path('manufacturing/actions/<str:action>', views.StageActionView.as_view()),
    path('manufacturing/ledger', views.LedgerListView.as_view()),
    path('manufacturing/ledger/<int:pk>/reverse', views.LedgerReverseView.as_view()),
]
