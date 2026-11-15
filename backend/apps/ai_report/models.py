# -*- coding: utf-8 -*-
import uuid

from django.db import models

from apps.accounts.models import Company, Post, User


class AiQueryLog(models.Model):
    """AI 报表问答留痕（谁问了什么、解析成什么意图）。

    只存问题与解析意图，不存查询结果（报表数字可随时重算，结果无审计价值；
    问题本身是用户输入，留痕用于滥用排查与白名单扩充依据）。
    """

    company = models.ForeignKey(Company, verbose_name='公司', on_delete=models.CASCADE,
                                related_name='ai_query_logs')
    user = models.ForeignKey(User, verbose_name='提问人', on_delete=models.CASCADE,
                             related_name='ai_query_logs')
    question = models.CharField('自然语言问题', max_length=200)
    intent = models.JSONField('解析意图', null=True, blank=True,
                              help_text='LLM 解析结果；None=未走 LLM（无 key/解析失败回落快捷问题）')
    ok = models.BooleanField('是否命中白名单报表', default=False)
    created_at = models.DateTimeField('提问时间', auto_now_add=True)

    class Meta:
        verbose_name = 'AI问答记录'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        indexes = [models.Index(fields=['company', 'user', 'created_at'])]

    def __str__(self):
        return f'{self.user_id}:{self.question[:20]}'


class AiQueryExecution(models.Model):
    """动态报表执行审计：只存计划与执行元数据，不复制业务结果。"""

    class Mode(models.TextChoices):
        PREVIEW = 'preview', '预览'
        EXPORT = 'export', '导出'

    class Status(models.TextChoices):
        SUCCESS = 'success', '成功'
        INVALID = 'invalid', '计划无效'
        DENIED = 'denied', '权限拒绝'
        ERROR = 'error', '执行错误'

    company = models.ForeignKey(Company, verbose_name='公司', on_delete=models.CASCADE,
                                related_name='ai_query_executions')
    user = models.ForeignKey(User, verbose_name='执行人', on_delete=models.CASCADE,
                             related_name='ai_query_executions')
    mode = models.CharField('执行方式', max_length=16, choices=Mode.choices)
    dataset = models.CharField('数据集', max_length=64, blank=True)
    plan = models.JSONField('规范化查询计划', default=dict)
    status = models.CharField('执行状态', max_length=16, choices=Status.choices)
    row_count = models.PositiveIntegerField('返回行数', default=0)
    total_count = models.PositiveIntegerField('结果总行数', default=0)
    truncated = models.BooleanField('是否截断', default=False)
    duration_ms = models.PositiveIntegerField('执行耗时毫秒', default=0)
    sql_count = models.PositiveSmallIntegerField('SQL条数', default=0)
    sql_time_ms = models.PositiveIntegerField('SQL耗时毫秒', default=0)
    slow = models.BooleanField('是否慢查询', default=False)
    error_code = models.CharField('错误代码', max_length=64, blank=True)
    created_at = models.DateTimeField('执行时间', auto_now_add=True)

    class Meta:
        verbose_name = 'AI动态报表执行记录'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        indexes = [models.Index(fields=['company', 'user', 'created_at'],
                                name='idx_ai_exec_co_user_time')]

    def __str__(self):
        return f'{self.company_id}:{self.user_id}:{self.dataset}:{self.status}'


class SavedReport(models.Model):
    """规范化 QueryPlan；分享配置不分享业务结果，执行仍按接收者实时鉴权。"""

    class Visibility(models.TextChoices):
        PRIVATE = 'private', '仅自己'
        COMPANY = 'company', '全公司'
        SELECTED = 'selected', '指定成员或岗位'

    company = models.ForeignKey(Company, verbose_name='公司', on_delete=models.CASCADE,
                                related_name='ai_saved_reports')
    user = models.ForeignKey(User, verbose_name='保存人', on_delete=models.CASCADE,
                             related_name='ai_saved_reports')
    name = models.CharField('报表名称', max_length=80)
    dataset = models.CharField('数据集', max_length=64)
    plan = models.JSONField('规范化查询计划', default=dict)
    visibility = models.CharField(
        '分享范围', max_length=16, choices=Visibility.choices,
        default=Visibility.PRIVATE)
    shared_at = models.DateTimeField('分享时间', null=True, blank=True)
    created_at = models.DateTimeField('保存时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = 'AI私有保存报表'
        verbose_name_plural = verbose_name
        ordering = ['-updated_at', '-id']
        constraints = [models.UniqueConstraint(
            fields=['company', 'user', 'name'], name='uniq_ai_saved_owner_name')]
        indexes = [models.Index(
            fields=['company', 'user', 'updated_at'], name='idx_ai_saved_owner_time')]

    def __str__(self):
        return f'{self.company_id}:{self.user_id}:{self.name}'


class SavedReportShare(models.Model):
    """指定分享 ACL；每行恰好指向同公司一个用户或一个岗位。"""

    report = models.ForeignKey(
        SavedReport, verbose_name='保存报表', on_delete=models.CASCADE,
        related_name='shares')
    user = models.ForeignKey(
        User, verbose_name='指定成员', on_delete=models.CASCADE,
        null=True, blank=True, related_name='shared_ai_reports')
    post = models.ForeignKey(
        Post, verbose_name='指定岗位', on_delete=models.CASCADE,
        null=True, blank=True, related_name='shared_ai_reports')
    created_at = models.DateTimeField('分享时间', auto_now_add=True)

    class Meta:
        verbose_name = 'AI报表指定分享'
        verbose_name_plural = verbose_name
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(user__isnull=False, post__isnull=True)
                    | models.Q(user__isnull=True, post__isnull=False)),
                name='ck_ai_share_exactly_one_target'),
            models.UniqueConstraint(
                fields=['report', 'user'], condition=models.Q(user__isnull=False),
                name='uniq_ai_share_report_user'),
            models.UniqueConstraint(
                fields=['report', 'post'], condition=models.Q(post__isnull=False),
                name='uniq_ai_share_report_post'),
        ]
        indexes = [
            models.Index(fields=['report', 'user'], name='idx_ai_share_report_user'),
            models.Index(fields=['report', 'post'], name='idx_ai_share_report_post'),
        ]

    def __str__(self):
        return f'{self.report_id}:{self.user_id or self.post_id}'


class AiExportTask(models.Model):
    """持久异步导出任务；文件路径由服务端生成，不暴露公开媒体 URL。"""

    class Status(models.TextChoices):
        PENDING = 'pending', '等待处理'
        RUNNING = 'running', '正在生成'
        SUCCESS = 'success', '可下载'
        FAILED = 'failed', '生成失败'
        EXPIRED = 'expired', '已过期'

    id = models.UUIDField('任务ID', primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, verbose_name='公司', on_delete=models.CASCADE,
        related_name='ai_export_tasks')
    user = models.ForeignKey(
        User, verbose_name='创建人', on_delete=models.CASCADE,
        related_name='ai_export_tasks')
    dataset = models.CharField('数据集', max_length=64)
    plan = models.JSONField('规范化查询计划', default=dict)
    status = models.CharField(
        '任务状态', max_length=16, choices=Status.choices, default=Status.PENDING)
    file_path = models.CharField('私有文件相对路径', max_length=255, blank=True)
    file_name = models.CharField('下载文件名', max_length=120, blank=True)
    error_code = models.CharField('错误代码', max_length=64, blank=True)
    error_message = models.CharField('用户可读错误', max_length=200, blank=True)
    row_count = models.PositiveIntegerField('导出返回行数', default=0)
    total_count = models.PositiveIntegerField('结果总行数', default=0)
    truncated = models.BooleanField('是否截断', default=False)
    attempts = models.PositiveSmallIntegerField('执行次数', default=0)
    execution = models.ForeignKey(
        AiQueryExecution, verbose_name='执行审计', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='export_tasks')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    started_at = models.DateTimeField('开始时间', null=True, blank=True)
    finished_at = models.DateTimeField('完成时间', null=True, blank=True)
    expires_at = models.DateTimeField('过期时间', null=True, blank=True)

    class Meta:
        verbose_name = 'AI异步导出任务'
        verbose_name_plural = verbose_name
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['company', 'user', 'created_at'],
                         name='idx_ai_export_user_time'),
            models.Index(fields=['status', 'created_at'], name='idx_ai_export_claim'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.user_id}:{self.id}:{self.status}'


# 定时报表模型独立成文件，末尾导入以保证 Django 模型/迁移自动检测。
from .schedule_models import AiReportSchedule, AiScheduledRun  # noqa: E402,F401