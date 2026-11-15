# -*- coding: utf-8 -*-
"""数据库持久 AI 定时报表 worker；不启动线程，不保存查询结果。"""
import calendar
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import connection, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import UserCompany
from apps.core.permissions import has_perm
from apps.ops.services import notify

from .catalog import can_use, get_dataset
from .execution_services import execute_governed_plan
from .models import AiQueryExecution, AiReportSchedule, AiScheduledRun, SavedReport
from .plan_schema import validate_plan

logger = logging.getLogger(__name__)


def local_zone(schedule):
    try:
        return ZoneInfo(schedule.timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValidationError({'timezone_name': '无效时区'}) from exc


def next_run_after(schedule, after=None):
    """返回严格晚于 after 的下一次本地日/周/月时间，并转为当前项目时区。

    月任务只在真实存在的日期执行：如 31 日任务跳过没有 31 日的月份，
    不借用 timedelta 滚入下个月。周任务的 weekday 采用 Python 约定（周一为 0）。
    """
    after = after or timezone.now()
    zone = local_zone(schedule)
    local_after = timezone.localtime(after, zone)
    hour, minute = schedule.hour, schedule.minute
    if (isinstance(hour, bool) or isinstance(minute, bool)
            or not isinstance(hour, int) or not isinstance(minute, int)
            or not 0 <= hour <= 23 or not 0 <= minute <= 59):
        raise ValidationError({'detail': '无效的本地执行时间'})

    def candidate(year, month, day):
        return datetime.combine(
            datetime(year, month, day).date(), time(hour, minute), tzinfo=zone)

    if schedule.frequency == AiReportSchedule.Frequency.DAILY:
        for offset in range(2):
            value = candidate(*(local_after.date() + timedelta(days=offset)).timetuple()[:3])
            if value > local_after:
                return value.astimezone(timezone.get_current_timezone())
    elif schedule.frequency == AiReportSchedule.Frequency.WEEKLY:
        if (isinstance(schedule.weekday, bool) or not isinstance(schedule.weekday, int)
                or not 0 <= schedule.weekday <= 6):
            raise ValidationError({'weekday': '周任务必须指定 0 至 6 的星期'})
        for offset in range(8):
            day = local_after.date() + timedelta(days=offset)
            if day.weekday() != schedule.weekday:
                continue
            value = candidate(day.year, day.month, day.day)
            if value > local_after:
                return value.astimezone(timezone.get_current_timezone())
    elif schedule.frequency == AiReportSchedule.Frequency.MONTHLY:
        day_number = schedule.day_of_month
        if (isinstance(day_number, bool) or not isinstance(day_number, int)
                or not 1 <= day_number <= 31):
            raise ValidationError({'day_of_month': '月任务日期必须为 1 至 31'})
        year, month = local_after.year, local_after.month
        # 最多检查 96 个月，足以跳过连续的短月，也防止坏数据导致死循环。
        for _ in range(96):
            if day_number <= calendar.monthrange(year, month)[1]:
                value = candidate(year, month, day_number)
                if value > local_after:
                    return value.astimezone(timezone.get_current_timezone())
            month += 1
            if month == 13:
                year, month = year + 1, 1
    else:
        raise ValidationError({'frequency': '仅支持 daily/weekly/monthly'})
    raise ValidationError({'detail': '无法计算下次执行时间'})


def _active_link(schedule):
    try:
        link = UserCompany.objects.select_related('user', 'company', 'post').get(
            company_id=schedule.company_id, user_id=schedule.user_id,
            status=UserCompany.Status.ACTIVE, is_deleted=False)
    except UserCompany.DoesNotExist as exc:
        raise PermissionDenied('所有者已不是本公司在职成员') from exc
    if not link.user.is_active or link.is_suspended:
        raise PermissionDenied('所有者账号已停用或停职')
    if not has_perm(link, 'ai.query'):
        raise PermissionDenied('AI 查询权限已变更')
    return link


def _plan_source(schedule):
    report = schedule.saved_report
    if report is not None:
        if report.company_id != schedule.company_id or report.user_id != schedule.user_id:
            raise PermissionDenied('保存报表所有权已变更')
        if report.visibility != SavedReport.Visibility.PRIVATE:
            link = _active_link(schedule)
            if not has_perm(link, 'ai.share'):
                raise PermissionDenied('AI 分享权限已变更')
        return report.plan
    if not isinstance(schedule.plan, dict):
        raise ValidationError({'plan': '缺少规范化查询计划'})
    return schedule.plan


def _audit(run, status, raw_plan, normalized=None, result=None,
           error_code='', performance=None):
    """复用现有执行审计；审计自身失败不能覆盖任务原状态。"""
    try:
        dataset = ''
        if isinstance(normalized, dict):
            dataset = normalized.get('dataset', '')
        elif isinstance(raw_plan, dict) and isinstance(raw_plan.get('dataset'), str):
            dataset = raw_plan['dataset']
        performance = performance or {}
        return AiQueryExecution.objects.create(
            company=run.schedule.company, user=run.schedule.user, mode='preview',
            dataset=dataset, plan=normalized or raw_plan or {}, status=status,
            row_count=result.row_count if result else 0,
            total_count=result.total_count if result else 0,
            truncated=result.truncated if result else False,
            sql_count=performance.get('sql_count', 0),
            sql_time_ms=performance.get('sql_time_ms', 0),
            slow=performance.get('slow', False), error_code=error_code)
    except Exception:
        logger.error('AI 定时报表执行审计写入失败：run=%s', run.id)
        return None


def _failure_notification(run):
    # 不包含异常文本、计划、报表名称、筛选值、行数或任何业务结果。
    try:
        notify(run.schedule.company, [run.schedule.user_id], 'ai_schedule_failed',
               '定时 AI 报表执行失败',
               f'定时任务 ID：{run.schedule_id} 执行失败，请检查权限和报表配置。')
    except Exception:
        logger.error('AI 定时报表失败通知写入失败：run=%s', run.id)


def _success_notification(schedule, recipient_ids):
    report = schedule.saved_report
    if report is not None:
        message = (f'定时 AI 报表已生成。已保存报表 ID：{report.id}，'
                   f'链接：/ai/report/saved/{report.id}。请打开报表查看。')
    else:
        message = f'定时 AI 报表已生成。定时任务 ID：{schedule.id}，请打开对应报表查看。'
    # 仅保存报表引用，不保存结果、行数、筛选条件或报表内容。
    notify(schedule.company, recipient_ids, 'ai_schedule_success',
           '定时 AI 报表已生成', message)


def _recipient_links(schedule):
    report = schedule.saved_report
    if report is None or report.visibility == SavedReport.Visibility.PRIVATE:
        return []
    links = UserCompany.objects.filter(
        company_id=schedule.company_id, status=UserCompany.Status.ACTIVE,
        is_deleted=False, is_suspended=False, user__is_active=True,
    ).exclude(user_id=schedule.user_id).select_related('user', 'post')
    if report.visibility == SavedReport.Visibility.SELECTED:
        from django.db.models import Q
        links = links.filter(
            Q(user__shared_ai_reports__report=report)
            | Q(post__shared_ai_reports__report=report)).distinct()
    return links


def _recipient_allowed(link, plan):
    """通知前逐接收者实时检查 ai.query、数据集权限和完整计划约束。"""
    if not has_perm(link, 'ai.query'):
        return False
    dataset = get_dataset(plan.get('dataset')) if isinstance(plan, dict) else None
    if dataset is None or not can_use(link, dataset.permission):
        return False
    try:
        validate_plan(plan, dataset, link, max_limit=500)
    except (PermissionDenied, ValidationError):
        return False
    return True


def _claim_next(now):
    """在事务内锁定并认领一条到期任务；PG 使用 SKIP LOCKED。"""
    with transaction.atomic():
        schedules = AiReportSchedule.objects.filter(
            enabled=True, next_run_at__lte=now,
        ).select_related('company', 'user', 'saved_report').order_by('next_run_at', 'id')
        if connection.features.has_select_for_update_skip_locked:
            # saved_report 是可空外联表；只锁任务主表，避免 PG 锁 nullable outer join 报错。
            schedules = schedules.select_for_update(of=('self',), skip_locked=True)
        else:
            schedules = schedules.select_for_update()
        schedule = schedules.first()
        if schedule is None:
            return None
        scheduled_at = schedule.next_run_at
        # 无论最终成功、撤权、计划失效或异常，都先推进到当前时间之后，避免轮询轰炸。
        schedule.next_run_at = next_run_after(schedule, max(now, scheduled_at))
        schedule.save(update_fields=['next_run_at', 'updated_at'])
        run, _created = AiScheduledRun.objects.get_or_create(
            schedule=schedule, scheduled_at=scheduled_at,
            defaults={'status': AiScheduledRun.Status.PENDING})
        if run.status != AiScheduledRun.Status.PENDING:
            return None
        run.status = AiScheduledRun.Status.RUNNING
        run.attempts += 1
        run.started_at = now
        run.error_code = ''
        run.save(update_fields=['status', 'attempts', 'started_at', 'error_code'])
        run.schedule = schedule
        return run


def _execute_schedule_run(run):
    """执行已认领的运行；所有入口都重新做治理、结构及接收者校验。"""
    schedule = run.schedule
    raw_plan = schedule.saved_report.plan if schedule.saved_report else schedule.plan
    normalized = None
    result = None
    performance = {}
    status = AiScheduledRun.Status.FAILED
    audit_status = 'error'
    error_code = 'execution_error'
    try:
        link = _active_link(schedule)
        raw_plan = _plan_source(schedule)
        _dataset, normalized, result, performance = execute_governed_plan(
            raw_plan, link, schedule.company, max_limit=500)
        recipients = [schedule.user_id]
        recipients.extend(
            link.user_id for link in _recipient_links(schedule)
            if _recipient_allowed(link, raw_plan))
        _success_notification(schedule, recipients)
        status = AiScheduledRun.Status.SUCCESS
        audit_status = 'success'
        error_code = ''
    except PermissionDenied:
        status = AiScheduledRun.Status.DENIED
        audit_status = 'denied'
        error_code = 'permission_denied'
    except ValidationError:
        status = AiScheduledRun.Status.INVALID
        audit_status = 'invalid'
        error_code = 'invalid_plan'
    except Exception as exc:
        # 异常文本可能含 SQL 参数或筛选值，不写审计或通知。
        logger.error('AI 定时报表执行失败：run=%s type=%s', run.id, type(exc).__name__)
    run.execution = _audit(
        run, audit_status, raw_plan, normalized, result, error_code, performance)
    run.status = status
    run.error_code = error_code
    run.finished_at = timezone.now()
    run.save(update_fields=['status', 'error_code', 'execution', 'finished_at'])
    if status != AiScheduledRun.Status.SUCCESS:
        _failure_notification(run)
    return run


def process_next_schedule(now=None):
    """原子认领并执行一条到期任务；返回 run ID，无到期任务返回 None。"""
    now = now or timezone.now()
    run = _claim_next(now)
    if run is None:
        return None
    return _execute_schedule_run(run).id


def run_schedule_now(schedule, request_id, retry_of=None):
    """创建并执行手动/重试运行，返回 ``(run, created)``。

    同一任务的请求键由数据库唯一约束兜底；锁任务后重查可让并发重复请求
    直接取得已有运行，不再执行或通知。重试来源也至多产生一条直接重试运行。
    """
    with transaction.atomic():
        locked = AiReportSchedule.objects.select_for_update(of=('self',)).select_related(
            'company', 'user', 'saved_report').get(pk=schedule.pk)
        existing = AiScheduledRun.objects.filter(
            schedule=locked, request_id=request_id).first()
        if existing is not None:
            expected_kind = (AiScheduledRun.Kind.RETRY if retry_of
                             else AiScheduledRun.Kind.MANUAL)
            if (existing.kind != expected_kind
                    or (retry_of is not None and existing.retry_of_id != retry_of.pk)):
                raise ValidationError({'request_id': '请求键已用于其他运行'})
            return existing, False
        source = None
        if retry_of is not None:
            source = AiScheduledRun.objects.select_for_update().filter(
                pk=retry_of.pk, schedule=locked).first()
            if source is None:
                raise ValidationError({'detail': '重试来源与任务不匹配'})
            if source.status not in {
                    AiScheduledRun.Status.FAILED, AiScheduledRun.Status.DENIED,
                    AiScheduledRun.Status.INVALID}:
                raise ValidationError({'detail': '只能重试失败的运行'})
            previous_retry = AiScheduledRun.objects.filter(retry_of=source).first()
            if previous_retry is not None:
                return previous_retry, False
        scheduled_at = timezone.now()
        while AiScheduledRun.objects.filter(
                schedule=locked, scheduled_at=scheduled_at).exists():
            scheduled_at += timedelta(microseconds=1)
        now = timezone.now()
        run = AiScheduledRun.objects.create(
            schedule=locked, scheduled_at=scheduled_at, request_id=request_id,
            kind=AiScheduledRun.Kind.RETRY if source else AiScheduledRun.Kind.MANUAL,
            retry_of=source, status=AiScheduledRun.Status.RUNNING, attempts=1,
            started_at=now)
        run.schedule = locked
    return _execute_schedule_run(run), True
