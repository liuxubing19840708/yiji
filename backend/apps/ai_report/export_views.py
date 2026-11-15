# -*- coding: utf-8 -*-
"""异步导出任务 API；下载不经过公开媒体地址。"""
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.mixins import CompanyContextMixin

from .catalog import can_use, get_dataset
from .export_services import export_file_path
from .models import AiExportTask
from .plan_schema import validate_plan


class ExportGone(APIException):
    status_code = 410
    default_detail = '导出文件已过期，请重新创建任务'
    default_code = 'export_expired'


def serialize_task(task):
    return {
        'id': str(task.id),
        'status': task.status,
        'dataset': task.dataset,
        'plan': task.plan,
        'file_name': task.file_name,
        'error_code': task.error_code,
        'error_message': task.error_message,
        'row_count': task.row_count,
        'total_count': task.total_count,
        'truncated': task.truncated,
        'created_at': task.created_at,
        'started_at': task.started_at,
        'finished_at': task.finished_at,
        'expires_at': task.expires_at,
        'download_url': (
            f'/api/ai/report/exports/{task.id}/download'
            if task.status == AiExportTask.Status.SUCCESS else None),
    }


class AiExportTaskMixin(CompanyContextMixin):
    def get_queryset(self):
        return AiExportTask.objects.filter(company=self.company, user=self.request.user)

    def get_object(self, pk):
        try:
            return self.get_queryset().get(pk=pk)
        except (AiExportTask.DoesNotExist, ValueError):
            raise Http404

    def normalized_plan(self, raw):
        if not isinstance(raw, dict):
            raise ValidationError({'plan': '必须是 JSON 对象'})
        dataset = get_dataset(raw.get('dataset'))
        if dataset is None:
            raise ValidationError({'plan': '不支持的数据集'})
        if not can_use(self.user_company, dataset.permission):
            raise PermissionDenied(f'无权导出数据集：{dataset.label}')
        return dataset, validate_plan(raw, dataset, self.user_company, max_limit=5000)


class AiExportTaskListView(AiExportTaskMixin, APIView):
    def get(self, request):
        self.check_perm('ai.export')
        return Response({'results': [
            serialize_task(task) for task in self.get_queryset()[:20]]})

    def post(self, request):
        self.check_perm('ai.export')
        if self.get_queryset().filter(status__in=[
                AiExportTask.Status.PENDING, AiExportTask.Status.RUNNING]).count() >= 3:
            raise ValidationError({'detail': '同时最多等待 3 个导出任务，请稍后重试'})
        raw_plan = request.data.get('plan') if isinstance(request.data, dict) else None
        dataset, plan = self.normalized_plan(raw_plan)
        task = AiExportTask.objects.create(
            company=self.company, user=request.user,
            dataset=dataset.key, plan=plan)
        return Response(serialize_task(task), status=202)


class AiExportTaskDetailView(AiExportTaskMixin, APIView):
    def get(self, request, pk):
        self.check_perm('ai.export')
        return Response(serialize_task(self.get_object(pk)))


class AiExportTaskDownloadView(AiExportTaskMixin, APIView):
    def get(self, request, pk):
        self.check_perm('ai.export')
        task = self.get_object(pk)
        if task.status == AiExportTask.Status.EXPIRED or (
                task.expires_at and task.expires_at <= timezone.now()):
            raise ExportGone
        if task.status != AiExportTask.Status.SUCCESS:
            raise ValidationError({'detail': '文件尚未生成，请查询任务状态'})
        # 下载时重新校验当前岗位和计划，不因任务曾成功而永久放行。
        _dataset, _plan = self.normalized_plan(task.plan)
        try:
            path = export_file_path(task)
            stream = path.open('rb')
        except (OSError, ValueError):
            raise Http404
        response = FileResponse(
            stream, as_attachment=True, filename=f'{task.file_name or "AI动态报表"}.xlsx',
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        if task.execution_id:
            response['X-Ai-Execution-Id'] = str(task.execution_id)
        return response
