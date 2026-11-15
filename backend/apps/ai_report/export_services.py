# -*- coding: utf-8 -*-
"""数据库持久异步导出 worker；可显式单步调用，不依赖裸线程或 on_commit。"""
import logging
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import UserCompany
from apps.core.permissions import has_perm

from .execution_services import execute_governed_plan
from .exporters import result_xlsx_bytes
from .models import AiExportTask, AiQueryExecution

logger = logging.getLogger(__name__)


def export_file_path(task):
    """只接受 worker 生成的固定相对路径，并再次约束在私有根目录内。"""
    root = Path(settings.AI_EXPORT_ROOT).resolve()
    path = (root / task.file_path).resolve()
    if root != path.parent.parent or path.name != f'{task.id}.xlsx':
        raise ValueError('非法导出文件路径')
    return path


def _audit(task, status, plan=None, result=None, error_code='', performance=None):
    try:
        performance = performance or {}
        return AiQueryExecution.objects.create(
            company=task.company, user=task.user, mode='export',
            dataset=task.dataset, plan=plan or task.plan, status=status,
            row_count=result.row_count if result else 0,
            total_count=result.total_count if result else 0,
            truncated=result.truncated if result else False,
            sql_count=performance.get('sql_count', 0),
            sql_time_ms=performance.get('sql_time_ms', 0),
            slow=performance.get('slow', False),
            error_code=error_code)
    except Exception:
        logger.error('AI 异步导出审计写入失败：task=%s', task.id)
        return None


def _claim_next():
    with transaction.atomic():
        queryset = AiExportTask.objects.filter(
            status=AiExportTask.Status.PENDING).select_related('company', 'user')
        from django.db import connection
        if connection.features.has_select_for_update_skip_locked:
            queryset = queryset.select_for_update(skip_locked=True)
        else:
            queryset = queryset.select_for_update()
        task = queryset.order_by('created_at', 'id').first()
        if task is None:
            return None
        task.status = AiExportTask.Status.RUNNING
        task.started_at = timezone.now()
        task.attempts += 1
        task.error_code = ''
        task.error_message = ''
        task.save(update_fields=[
            'status', 'started_at', 'attempts', 'error_code', 'error_message'])
        return task


def process_next_export():
    """原子认领并处理一条任务；返回任务 ID，无待处理任务返回 None。"""
    task = _claim_next()
    if task is None:
        return None
    normalized = None
    result = None
    performance = {}
    status = 'error'
    error_code = 'execution_error'
    try:
        link = UserCompany.objects.select_related('post').get(
            company=task.company, user=task.user,
            status=UserCompany.Status.ACTIVE, is_deleted=False)
        if not task.user.is_active or not has_perm(link, 'ai.export'):
            raise PermissionDenied('导出权限已变更')
        _dataset, normalized, result, performance = execute_governed_plan(
            task.plan, link, task.company, max_limit=5000)
        content = result_xlsx_bytes(result)
        relative = f'{task.company_id}/{task.id}.xlsx'
        task.file_path = relative
        path = export_file_path(task)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f'.{path.name}.tmp')
        temporary.write_bytes(content)
        temporary.replace(path)
        task.file_name = result.title[:120]
        task.row_count = result.row_count
        task.total_count = result.total_count
        task.truncated = result.truncated
        task.status = AiExportTask.Status.SUCCESS
        task.expires_at = timezone.now() + timedelta(
            hours=settings.AI_EXPORT_RETENTION_HOURS)
        status = 'success'
        error_code = ''
    except PermissionDenied:
        task.status = AiExportTask.Status.FAILED
        task.error_message = '导出或数据权限已变更，请重新创建导出任务'
        status = 'denied'
        error_code = 'permission_denied'
    except ValidationError:
        task.status = AiExportTask.Status.FAILED
        task.error_message = '保存的查询计划已失效，请调整条件后重新导出'
        status = 'invalid'
        error_code = 'invalid_plan'
    except Exception as exc:
        task.status = AiExportTask.Status.FAILED
        task.error_message = '文件生成失败，请稍后重新创建导出任务'
        # 不记录异常文本，避免数据库错误携带 SQL 参数或业务筛选值进入日志。
        logger.error('AI 异步导出失败：task=%s type=%s', task.id, type(exc).__name__)
    task.execution = _audit(
        task, status, normalized, result, error_code, performance)
    task.error_code = error_code
    task.finished_at = timezone.now()
    task.save(update_fields=[
        'status', 'file_path', 'file_name', 'error_code', 'error_message',
        'row_count', 'total_count', 'truncated', 'execution', 'finished_at',
        'expires_at'])
    return task.id


def expire_old_exports(now=None):
    """清理过期私有文件；任务元数据保留用于状态查询。"""
    now = now or timezone.now()
    tasks = AiExportTask.objects.filter(
        status=AiExportTask.Status.SUCCESS, expires_at__lte=now)
    count = 0
    for task in tasks:
        if task.file_path:
            try:
                export_file_path(task).unlink(missing_ok=True)
            except (OSError, ValueError):
                logger.error('AI 过期导出文件清理失败：task=%s', task.id)
        task.status = AiExportTask.Status.EXPIRED
        task.file_path = ''
        task.save(update_fields=['status', 'file_path'])
        count += 1
    return count
