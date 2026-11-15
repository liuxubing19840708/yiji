"""产前域路由；主线程在 config.urls 的 /api/ 下 include 即可。"""
from django.urls import path

from . import views

urlpatterns = [
    path('preproduction/templates', views.CraftTemplateListCreateView.as_view()),
    path('preproduction/templates/<int:pk>', views.CraftTemplateDetailView.as_view()),
    path('preproduction/tech-packs', views.TechPackListCreateView.as_view()),
    path('preproduction/tech-packs/<int:pk>', views.TechPackDetailView.as_view()),
    path('preproduction/tech-packs/<int:pack_id>/samples', views.SampleTaskListCreateView.as_view()),
    path('preproduction/samples/<int:pk>/records', views.SampleRecordCreateView.as_view()),
    path('preproduction/tech-packs/<int:pack_id>/issues', views.ReviewIssueListCreateView.as_view()),
    path('preproduction/issues/<int:pk>', views.ReviewIssueActionView.as_view()),
    path('preproduction/tech-packs/<int:pack_id>/briefing-targets', views.BriefingTargetListCreateView.as_view()),
    path('preproduction/briefing-targets/<int:pk>/sign', views.BriefingSignView.as_view()),
    path('preproduction/tech-packs/<int:pack_id>/briefing-confirmation', views.BriefingConfirmationView.as_view()),
    path('preproduction/tech-packs/<int:pk>/approval', views.ApprovalDecisionView.as_view()),
    path('preproduction/orders/<int:order_id>/status', views.OrderApprovalStatusView.as_view()),
    path('preproduction/tech-packs/<int:pack_id>/audit-events', views.AuditEventListView.as_view()),
]
