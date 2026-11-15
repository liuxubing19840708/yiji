# -*- coding: utf-8 -*-
"""AI 定时报表所有者 CRUD；管理范围不随报表分享扩大。"""
import uuid

from django.conf import settings
from django.http import Http404
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.mixins import CompanyContextMixin
from apps.core.permissions import has_perm

from .catalog import can_use, get_dataset
from .models import AiReportSchedule, AiScheduledRun, SavedReport
from .plan_schema import validate_plan
from .schedule_services import local_zone, next_run_after, run_schedule_now


def serialize_schedule(instance):
    report = instance.saved_report
    return {
        'id': instance.id,
        'name': instance.name,
        'saved_report_id': instance.saved_report_id,
        'saved_report': ({'id': report.id, 'name': report.name,
                          'visibility': report.visibility} if report else None),
        'plan': instance.plan,
        'frequency': instance.frequency,
        'hour': instance.hour,
        'minute': instance.minute,
        'weekday': instance.weekday,
        'day_of_month': instance.day_of_month,
        'timezone': instance.timezone_name,
        'next_run_at': instance.next_run_at,
        'enabled': instance.enabled,
        'created_at': instance.created_at,
        'updated_at': instance.updated_at,
    }


RUN_ERROR_SUMMARIES = {
    'permission_denied': '权限或成员状态已变化，请检查任务权限',
    'invalid_plan': '报表配置已失效，请检查任务配置',
    'execution_error': '执行失败，请稍后重试',
}


def serialize_run(instance):
    """仅返回安全运行元数据，不返回计划、结果、行数或异常文本。"""
    failed = instance.status in {
        AiScheduledRun.Status.FAILED, AiScheduledRun.Status.DENIED,
        AiScheduledRun.Status.INVALID,
    }
    return {
        'id': instance.id,
        'schedule_id': instance.schedule_id,
        'schedule_name': instance.schedule.name,
        'kind': instance.kind,
        'scheduled_at': instance.scheduled_at,
        'started_at': instance.started_at,
        'finished_at': instance.finished_at,
        'status': instance.status,
        'outcome': ('success' if instance.status == AiScheduledRun.Status.SUCCESS
                    else 'failed' if failed else 'running'),
        'error_code': instance.error_code,
        'error_summary': RUN_ERROR_SUMMARIES.get(instance.error_code, ''),
        'retry_of_id': instance.retry_of_id,
    }


class ScheduleMixin(CompanyContextMixin):
    def get_queryset(self):
        return AiReportSchedule.objects.filter(
            company=self.company, user=self.request.user).select_related('saved_report')

    def get_object(self, pk):
        # 非所有者和其他公司统一 404，不泄露任务是否存在。
        try:
            return self.get_queryset().get(pk=pk)
        except AiReportSchedule.DoesNotExist as exc:
            raise Http404 from exc

    def check_query(self):
        if not has_perm(self.user_company, 'ai.query'):
            raise PermissionDenied('没有 AI 查询权限')

    @staticmethod
    def _integer(data, key, default, minimum, maximum):
        value = data.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValidationError({key: f'必须为 {minimum} 至 {maximum} 的整数'})
        return value

    def normalized_values(self, data, instance=None):
        if not isinstance(data, dict):
            raise ValidationError({'detail': '请求体必须是 JSON 对象'})
        self.check_query()
        raw_name = data.get('name', instance.name if instance else '')
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValidationError({'name': '任务名称不能为空'})
        name = raw_name.strip()
        if len(name) > 80:
            raise ValidationError({'name': '任务名称不能超过 80 字'})

        report_id = data.get(
            'saved_report_id', instance.saved_report_id if instance else None)
        report = None
        plan = None
        if report_id is not None:
            if isinstance(report_id, bool) or not isinstance(report_id, int) or report_id <= 0:
                raise ValidationError({'saved_report_id': '必须为有效保存报表 ID'})
            try:
                report = SavedReport.objects.get(
                    id=report_id, company=self.company, user=self.request.user)
            except SavedReport.DoesNotExist as exc:
                raise Http404 from exc
            raw_plan = report.plan
        else:
            raw_plan = data.get('plan', instance.plan if instance else None)
        if not isinstance(raw_plan, dict):
            raise ValidationError({'plan': '原始计划任务必须提供 plan'})
        dataset = get_dataset(raw_plan.get('dataset'))
        if dataset is None:
            raise ValidationError({'plan': '不支持的数据集'})
        if not can_use(self.user_company, dataset.permission):
            raise PermissionDenied('无权查询该数据集')
        normalized = validate_plan(raw_plan, dataset, self.user_company, max_limit=500)
        if report is None:
            plan = normalized
        if report is not None and report.visibility != SavedReport.Visibility.PRIVATE:
            if not has_perm(self.user_company, 'ai.share'):
                raise PermissionDenied('分享报表的定时任务需要 AI 分享权限')

        frequency = data.get('frequency', instance.frequency if instance else 'daily')
        if frequency not in AiReportSchedule.Frequency.values:
            raise ValidationError({'frequency': '仅支持 daily/weekly/monthly'})
        hour = self._integer(data, 'hour', instance.hour if instance else 9, 0, 23)
        minute = self._integer(data, 'minute', instance.minute if instance else 0, 0, 59)
        weekday = self._integer(
            data, 'weekday', instance.weekday if instance else None, 0, 6
        ) if frequency == AiReportSchedule.Frequency.WEEKLY else None
        day_of_month = self._integer(
            data, 'day_of_month', instance.day_of_month if instance else None, 1, 31
        ) if frequency == AiReportSchedule.Frequency.MONTHLY else None
        timezone_name = data.get(
            'timezone', instance.timezone_name if instance else settings.TIME_ZONE)
        if not isinstance(timezone_name, str) or not timezone_name or len(timezone_name) > 64:
            raise ValidationError({'timezone': '必须为有效时区名称'})
        enabled = data.get('enabled', instance.enabled if instance else True)
        if not isinstance(enabled, bool):
            raise ValidationError({'enabled': '必须为布尔值'})
        values = {
            'name': name, 'saved_report': report, 'plan': plan,
            'frequency': frequency, 'hour': hour, 'minute': minute,
            'weekday': weekday, 'day_of_month': day_of_month,
            'timezone_name': timezone_name, 'enabled': enabled,
        }
        probe = AiReportSchedule(**values)
        local_zone(probe)
        return values


class AiScheduleListView(ScheduleMixin, APIView):
    def get(self, request):
        self.check_query()
        return Response({'results': [serialize_schedule(item) for item in self.get_queryset()]})

    def post(self, request):
        if self.get_queryset().count() >= 100:
            raise ValidationError({'detail': '每个公司账号最多创建 100 个定时任务'})
        values = self.normalized_values(request.data)
        instance = AiReportSchedule(company=self.company, user=request.user, **values)
        instance.next_run_at = next_run_after(instance)
        instance.save()
        instance = self.get_queryset().get(pk=instance.pk)
        return Response(serialize_schedule(instance), status=201)


class AiScheduleDetailView(ScheduleMixin, APIView):
    def get(self, request, pk):
        instance = self.get_object(pk)
        self.check_query()
        return Response(serialize_schedule(instance))

    def patch(self, request, pk):
        instance = self.get_object(pk)
        values = self.normalized_values(request.data, instance)
        for key, value in values.items():
            setattr(instance, key, value)
        instance.next_run_at = next_run_after(instance)
        instance.save()
        instance = self.get_queryset().get(pk=instance.pk)
        return Response(serialize_schedule(instance))

    def put(self, request, pk):
        return self.patch(request, pk)

    def delete(self, request, pk):
        instance = self.get_object(pk)
        self.check_query()
        instance.delete()
        return Response(status=204)


class AiScheduleRunListView(ScheduleMixin, APIView):
    def get(self, request):
        self.check_query()
        runs = AiScheduledRun.objects.filter(
            schedule__company=self.company, schedule__user=request.user,
        ).select_related('schedule')
        schedule_id = request.query_params.get('schedule_id')
        if schedule_id not in (None, ''):
            if not schedule_id.isdigit():
                raise ValidationError({'schedule_id': '必须为有效任务 ID'})
            runs = runs.filter(schedule_id=int(schedule_id))
        try:
            limit = int(request.query_params.get('limit', 50))
        except (TypeError, ValueError) as exc:
            raise ValidationError({'limit': '必须为 1 至 100 的整数'}) from exc
        if not 1 <= limit <= 100:
            raise ValidationError({'limit': '必须为 1 至 100 的整数'})
        return Response({'results': [serialize_run(run) for run in runs[:limit]]})


class AiScheduleManualRunView(ScheduleMixin, APIView):
    def post(self, request, pk):
        instance = self.get_object(pk)
        self.check_query()
        request_id = parse_request_id(request.data)
        run, created = run_schedule_now(instance, request_id)
        return Response(serialize_run(run), status=201 if created else 200)


class AiScheduleRunRetryView(ScheduleMixin, APIView):
    def post(self, request, pk):
        self.check_query()
        try:
            source = AiScheduledRun.objects.select_related(
                'schedule', 'schedule__company', 'schedule__user',
                'schedule__saved_report').get(
                    pk=pk, schedule__company=self.company, schedule__user=request.user)
        except AiScheduledRun.DoesNotExist as exc:
            raise Http404 from exc
        request_id = parse_request_id(request.data)
        run, created = run_schedule_now(
            source.schedule, request_id, retry_of=source)
        return Response(serialize_run(run), status=201 if created else 200)


def parse_request_id(data):
    if not isinstance(data, dict):
        raise ValidationError({'detail': '请求体必须是 JSON 对象'})
    value = data.get('request_id')
    if not isinstance(value, str):
        raise ValidationError({'request_id': '必须提供 UUID 请求幂等键'})
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValidationError({'request_id': '必须为有效 UUID'}) from exc
