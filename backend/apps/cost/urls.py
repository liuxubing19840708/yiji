"""订单成本路由（无尾斜杠，契约 §四；全部挂公司隔离 + 权限点校验）。"""
from django.urls import path

from apps.cost import views

urlpatterns = [
    # 批量重算须在 <int:order_id> 前注册（字面段不会被 int 转换器吞，但先读更稳）
    path('cost/orders/recompute-batch', views.OrderCostRecomputeBatchView.as_view()),
    path('cost/orders', views.OrderCostListView.as_view()),
    # 损益视图（评审 Top10 #2）：聚合路由须在 <int:order_id> 前注册
    path('cost/orders/pnl', views.OrderPnlListView.as_view()),
    path('cost/orders/<int:order_id>/recompute', views.OrderCostRecomputeView.as_view()),
    path('cost/orders/<int:order_id>/overhead', views.OrderCostOverheadView.as_view()),
    path('cost/wage-reconciliation', views.WageReconciliationView.as_view()),
]
