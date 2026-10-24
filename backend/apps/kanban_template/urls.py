from django.urls import path

from apps.kanban_template import views, web

urlpatterns = [
    path('kanban/fields', views.KanbanFieldListView.as_view()),
    path('kanban/templates', views.KanbanTemplateListCreateView.as_view()),
    path('kanban/templates/<int:pk>', views.KanbanTemplateDetailView.as_view()),
    path('kanban/templates/<int:pk>/default', views.KanbanTemplateDefaultView.as_view()),
    path('kanban/import-header', views.KanbanImportHeaderView.as_view()),
    path('kanban/preview', views.KanbanPreviewView.as_view()),
    path('kanban/render', views.KanbanRenderView.as_view()),
    # Vue3 配置端（构建产物静态托管，与 Django 单体同源）
    path('kanban-admin', web.spa_index),
    path('kanban-admin/<path:asset>', web.spa_asset),
]
