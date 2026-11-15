# -*- coding: utf-8 -*-
from django.urls import path

from . import export_views, plan_views, saved_views, schedule_views, views

urlpatterns = [
    path('ai/report/query', views.AiReportQueryView.as_view()),
    path('ai/report/catalog', plan_views.AiReportCatalogView.as_view()),
    path('ai/report/execute', plan_views.AiReportExecuteView.as_view()),
    path('ai/report/export', plan_views.AiReportExportView.as_view()),
    path('ai/report/exports', export_views.AiExportTaskListView.as_view()),
    path('ai/report/exports/<uuid:pk>', export_views.AiExportTaskDetailView.as_view()),
    path('ai/report/exports/<uuid:pk>/download', export_views.AiExportTaskDownloadView.as_view()),
    path('ai/report/saved', saved_views.AiSavedReportListView.as_view()),
    path('ai/report/saved/targets', saved_views.AiSavedReportShareTargetsView.as_view()),
    path('ai/report/saved/<int:pk>', saved_views.AiSavedReportDetailView.as_view()),
    path('ai/report/saved/<int:pk>/share', saved_views.AiSavedReportShareView.as_view()),
    path('ai/report/saved/<int:pk>/execute', saved_views.AiSavedReportExecuteView.as_view()),
    path('ai/report/schedules', schedule_views.AiScheduleListView.as_view()),
    path('ai/report/schedules/runs', schedule_views.AiScheduleRunListView.as_view()),
    path('ai/report/schedules/runs/<int:pk>/retry', schedule_views.AiScheduleRunRetryView.as_view()),
    path('ai/report/schedules/<int:pk>/run', schedule_views.AiScheduleManualRunView.as_view()),
    path('ai/report/schedules/<int:pk>', schedule_views.AiScheduleDetailView.as_view()),
]
