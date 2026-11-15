"""技术管理路由（无尾斜杠，契约 §四；全部挂公司隔离 + 权限点校验）。"""
from django.urls import path

from apps.tech import views

urlpatterns = [
    # 物料主档（tech.material）
    path('tech/materials', views.MaterialListCreateView.as_view()),
    path('tech/materials/import', views.MaterialImportView.as_view()),
    path('tech/materials/<int:pk>', views.MaterialDetailView.as_view()),
    # 版式BOM（tech.bom）
    path('tech/boms', views.BomListCreateView.as_view()),
    path('tech/boms/import', views.BomImportView.as_view()),
    path('tech/boms/<int:pk>', views.BomDetailView.as_view()),
    path('tech/boms/<int:pk>/activate', views.BomActivateView.as_view()),
    path('tech/boms/<int:pk>/fork', views.BomForkView.as_view()),
    path('tech/boms/<int:pk>/references', views.BomReferencesView.as_view()),
]
