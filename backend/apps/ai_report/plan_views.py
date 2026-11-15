# -*- coding: utf-8 -*-
"""动态 QueryPlan API；与旧自然语言问报视图隔离，便于分阶段迁移。"""
import json
import logging
import time

from django.conf import settings

from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.mixins import CompanyContextMixin

from .catalog import CATALOG_VERSION, catalog_for
from .execution_services import execute_governed_plan
from .exporters import result_xlsx
from .models import AiQueryExecution
from .query_engine import serialize_report

logger = logging.getLogger(__name__)


class AiReportCatalogView(CompanyContextMixin, APIView):
    def get(self, request):
        return Response(catalog_for(self.user_company))


class PlanExecutionMixin:
    preview_limit = 500
    export_limit = 5000

    def _audit_plan(self, raw):
        try:
            text = json.dumps(raw, ensure_ascii=False)
        except (TypeError, ValueError):
            return {'invalid': str(raw)[:2000]}
        if len(text) <= 20000:
            return raw if isinstance(raw, dict) else {'invalid': raw}
        return {'truncated': text[:20000]}

    def _write_log(self, request, mode, raw_plan, normalized_plan, status,
                   dataset='', result=None, error_code='', duration_ms=0,
                   performance=None):
        try:
            performance = performance or {}
            execution = AiQueryExecution.objects.create(
                company=self.company,
                user=request.user,
                mode=mode,
                dataset=dataset if isinstance(dataset, str) else '',
                plan=normalized_plan or self._audit_plan(raw_plan),
                status=status,
                row_count=result.row_count if result else 0,
                total_count=result.total_count if result else 0,
                truncated=result.truncated if result else False,
                duration_ms=duration_ms,
                sql_count=performance.get('sql_count', 0),
                sql_time_ms=performance.get('sql_time_ms', 0),
                slow=performance.get('slow', False),
                error_code=error_code,
            )
            return execution.id
        except Exception:
            logger.exception('AI 动态报表执行审计写入失败')
            return None

    def execute(self, request, mode, raw_plan_override=None):
        started = time.monotonic()
        raw_plan = (raw_plan_override if raw_plan_override is not None else
                    request.data.get('plan') if isinstance(request.data, dict) else None)
        raw_dataset = raw_plan.get('dataset', '') if isinstance(raw_plan, dict) else ''
        if not isinstance(raw_dataset, str):
            raw_dataset = ''
        normalized_plan = None
        result = None
        performance = {}

        def elapsed():
            return max(0, round((time.monotonic() - started) * 1000))

        try:
            if mode == 'export':
                self.check_perm('ai.export')
            if not isinstance(raw_dataset, str) or not raw_dataset:
                raise ValidationError({'plan': '必须指定 dataset'})
            dataset, normalized_plan, result, performance = execute_governed_plan(
                raw_plan, self.user_company, self.company,
                max_limit=self.export_limit if mode == 'export' else self.preview_limit)
            performance['slow'] = performance['slow'] or elapsed() >= settings.AI_REPORT_SLOW_MS
            if performance['slow']:
                # 只记录元数据，不记录 SQL、绑定参数、筛选值或业务结果。
                logger.warning(
                    'AI 报表慢查询：dataset=%s duration_ms=%s sql_count=%s sql_time_ms=%s',
                    dataset.key, elapsed(), performance['sql_count'], performance['sql_time_ms'])

            if mode == 'export':
                response = result_xlsx(result)
                execution_id = self._write_log(
                    request, mode, raw_plan, normalized_plan, 'success',
                    dataset=dataset.key, result=result, duration_ms=elapsed(), performance=performance)
                if execution_id:
                    response['X-Ai-Execution-Id'] = str(execution_id)
                response['X-Ai-Sql-Count'] = str(performance['sql_count'])
                response['X-Ai-Sql-Time-Ms'] = str(performance['sql_time_ms'])
                return response

            execution_id = self._write_log(
                request, mode, raw_plan, normalized_plan, 'success',
                dataset=dataset.key, result=result, duration_ms=elapsed(), performance=performance)
            return Response({
                'mode': 'query_plan',
                'catalog_version': CATALOG_VERSION,
                'execution_id': execution_id,
                'plan': normalized_plan,
                'performance': performance,
                'report': serialize_report(result),
            })
        except PermissionDenied:
            self._write_log(
                request, mode, raw_plan, normalized_plan, 'denied',
                dataset=raw_dataset, result=result,
                error_code='permission_denied', duration_ms=elapsed(), performance=performance)
            raise
        except ValidationError:
            self._write_log(
                request, mode, raw_plan, normalized_plan, 'invalid',
                dataset=raw_dataset, result=result,
                error_code='invalid_plan', duration_ms=elapsed(), performance=performance)
            raise
        except Exception:
            self._write_log(
                request, mode, raw_plan, normalized_plan, 'error',
                dataset=raw_dataset, result=result,
                error_code='execution_error', duration_ms=elapsed(), performance=performance)
            logger.exception('AI 动态报表执行失败：dataset=%s', raw_dataset)
            raise


class AiReportExecuteView(PlanExecutionMixin, CompanyContextMixin, APIView):
    def post(self, request):
        return self.execute(request, 'preview')


class AiReportExportView(PlanExecutionMixin, CompanyContextMixin, APIView):
    def post(self, request):
        return self.execute(request, 'export')
