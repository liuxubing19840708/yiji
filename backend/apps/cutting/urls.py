"""主路由可 include('apps.cutting.urls')，建议前缀 api/cutting/。"""
from django.urls import path

from .views import HandoverCancelView, HandoverDetailView, HandoverListCreateView, HandoverSignView

app_name = 'cutting'

urlpatterns = [
    path('handovers/', HandoverListCreateView.as_view(), name='handover-list'),
    path('handovers/<int:pk>/', HandoverDetailView.as_view(), name='handover-detail'),
    path('handovers/<int:pk>/sign/', HandoverSignView.as_view(), name='handover-sign'),
    path('handovers/<int:pk>/cancel/', HandoverCancelView.as_view(), name='handover-cancel'),
]
