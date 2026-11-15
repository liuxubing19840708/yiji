"""主路由可 include('apps.handover.urls')，前缀使用 api/。"""
from django.urls import path

from .views import (HandoverBatchView, HandoverDetailView, HandoverListCreateView,
                    HandoverReassignView, HandoverRejectView, HandoverReceiveView,
                    PolicyDetailView, PolicyListCreateView)

app_name = 'handover'

urlpatterns = [
    path('handovers/policies', PolicyListCreateView.as_view(), name='policy-list'),
    path('handovers/policies/<int:pk>', PolicyDetailView.as_view(), name='policy-detail'),
    path('handovers/', HandoverListCreateView.as_view(), name='handover-list'),
    path('handovers/batches/<str:batch_no>/', HandoverBatchView.as_view(), name='handover-batch'),
    path('handovers/<int:pk>/', HandoverDetailView.as_view(), name='handover-detail'),
    path('handovers/<int:pk>/receive', HandoverReceiveView.as_view(), name='handover-receive'),
    path('handovers/<int:pk>/reject', HandoverRejectView.as_view(), name='handover-reject'),
    path('handovers/<int:pk>/reassign', HandoverReassignView.as_view(), name='handover-reassign'),
]
