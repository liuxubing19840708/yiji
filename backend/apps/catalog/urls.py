"""基础档案路由（无尾斜杠）。"""
from django.urls import path

from apps.catalog import views

urlpatterns = [
    path('styles', views.StyleListCreateView.as_view()),
    path('styles/<int:pk>', views.StyleDetailView.as_view()),
    path('styles/<int:pk>/copy', views.StyleCopyView.as_view()),
    path('styles/<int:pk>/sync-process', views.StyleSyncProcessView.as_view()),

    path('processes', views.ProcessListCreateView.as_view()),
    path('processes/<int:pk>', views.ProcessDetailView.as_view()),
    path('process-selector', views.ProcessSelectorView.as_view()),

    path('process-templates', views.ProcessTemplateListCreateView.as_view()),
    path('process-templates/<int:pk>', views.ProcessTemplateDetailView.as_view()),

    path('customer-groups', views.CustomerGroupListCreateView.as_view()),
    path('customer-groups/<int:pk>', views.CustomerGroupDetailView.as_view()),
    path('customers', views.CustomerListCreateView.as_view()),
    path('customers/<int:pk>', views.CustomerDetailView.as_view()),

    path('tickets', views.TicketListView.as_view()),
    path('tickets/batch', views.TicketBatchView.as_view()),
    path('tickets/print', views.TicketPrintView.as_view()),
    path('tickets/<int:pk>', views.TicketDetailView.as_view()),
]
