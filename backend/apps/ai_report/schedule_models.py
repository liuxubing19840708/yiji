# -*- coding: utf-8 -*-
"""AI 定时报表模型；由 models.py 末尾导入以纳入迁移检测。"""
from django.conf import settings
from django.db import models

from apps.accounts.models import Company, User


class AiReportSchedule(models.Model):
    """属于所有者的定时查询配置，不保存任何业务查询结果。"""

    class Frequency(models.TextChoices):
        DAILY = 'daily', '每天'
        WEEKLY = 'weekly', '每周'
        MONTHLY = 'monthly', '每月'

    company = models.ForeignKey(
        Company, verbose_name='公司', on_delete=models.CASCADE,
        related_name='ai_report_schedules')
    user = models.ForeignKey(
        User, verbose_name='所有者', on_delete=models.CASCADE,
        related_name='ai_report_schedules')
    saved_report = models.ForeignKey(
        'ai_report.SavedReport', verbose_name='已保存报表', on_delete=models.CASCADE,
        null=True, blank=True, related_name='schedules')
    plan = models.JSONField('规范化原始查询计划', null=True, blank=True, default=None)
    name = models.CharField('任务名称', max_length=80)
    frequency = models.CharField(
        '频率', max_length=16, choices=Frequency.choices)
    hour = models.PositiveSmallIntegerField('小时')
    minute = models.PositiveSmallIntegerField('分钟')
    weekday = models.PositiveSmallIntegerField('星期（0=周一）', null=True, blank=True)
    day_of_month = models.PositiveSmallIntegerField('每月日期', null=True, blank=True)
    timezone_name = models.CharField(
        '本地时区', max_length=64, default=settings.TIME_ZONE)
    next_run_at = models.DateTimeField('下次执行时间', db_index=True)
    enabled = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = 'AI定时报表任务'
        verbose_name_plural = verbose_name
        ordering = ['-updated_at', '-id']
        constraints = [models.CheckConstraint(
            condition=(
                models.Q(saved_report__isnull=False, plan__isnull=True)
                | models.Q(saved_report__isnull=True, plan__isnull=False)),
            name='ck_ai_schedule_one_plan_source')]
        indexes = [
            models.Index(fields=['company', 'user', 'updated_at'],
                         name='idx_ai_sched_owner_time'),
            models.Index(fields=['enabled', 'next_run_at'], name='idx_ai_sched_due'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.user_id}:{self.name}'


class AiScheduledRun(models.Model):
    """自然、手动及重试运行审计；历史运行完成后不可被重试改写。"""

    class Kind(models.TextChoices):
        NATURAL = 'natural', '自然调度'
        MANUAL = 'manual', '手动执行'
        RETRY = 'retry', '失败重试'

    class Status(models.TextChoices):
        PENDING = 'pending', '等待执行'
        RUNNING = 'running', '正在执行'
        SUCCESS = 'success', '执行成功'
        DENIED = 'denied', '权限拒绝'
        INVALID = 'invalid', '计划无效'
        FAILED = 'failed', '执行失败'

    schedule = models.ForeignKey(
        AiReportSchedule, verbose_name='定时任务', on_delete=models.CASCADE,
        related_name='runs')
    scheduled_at = models.DateTimeField('计划执行时间')
    kind = models.CharField(
        '运行类型', max_length=16, choices=Kind.choices, default=Kind.NATURAL)
    request_id = models.UUIDField('请求幂等键', null=True, blank=True)
    retry_of = models.OneToOneField(
        'self', verbose_name='重试来源', null=True, blank=True,
        on_delete=models.CASCADE, related_name='retry_run')
    status = models.CharField(
        '执行状态', max_length=16, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField('认领次数', default=0)
    error_code = models.CharField('错误代码', max_length=64, blank=True)
    execution = models.ForeignKey(
        'ai_report.AiQueryExecution', verbose_name='查询执行审计', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='scheduled_runs')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    started_at = models.DateTimeField('开始时间', null=True, blank=True)
    finished_at = models.DateTimeField('完成时间', null=True, blank=True)

    class Meta:
        verbose_name = 'AI定时报表执行审计'
        verbose_name_plural = verbose_name
        ordering = ['-scheduled_at', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['schedule', 'scheduled_at'], name='uniq_ai_sched_run_time'),
            models.UniqueConstraint(
                fields=['schedule', 'request_id'], condition=models.Q(request_id__isnull=False),
                name='uniq_ai_sched_run_request'),
        ]
        indexes = [
            models.Index(fields=['status', 'scheduled_at'], name='idx_ai_sched_run_claim'),
            models.Index(fields=['schedule', 'created_at'], name='idx_ai_sched_run_owner'),
        ]

    def __str__(self):
        return f'{self.schedule_id}:{self.scheduled_at}:{self.status}'
