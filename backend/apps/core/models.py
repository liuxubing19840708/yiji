"""公共模型：软删除基类与默认管理器。"""
from django.db import models


class ActiveManager(models.Manager):
    """只返回未删除对象。"""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class AllManager(models.Manager):
    """包含已删除对象。"""


class BaseModel(models.Model):
    """业务模型基类：软删除 + 创建/更新时间。

    查询请使用 objects（默认过滤 is_deleted=False）；
    需要包含已删除数据时用 all_objects。
    """

    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    is_deleted = models.BooleanField('是否已删除', default=False, db_index=True)

    objects = ActiveManager()
    all_objects = AllManager()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        """软删除：仅标记，不物理删除。"""
        self.is_deleted = True
        self.save(update_fields=['is_deleted', 'updated_at'])


class ClientLog(BaseModel):
    """客户端日志上报（JS 异常/未处理 Promise/接口失败/关键动线埋点）。

    厂→用户隔离：登录态上报带 company/user（读接口按请求者公司过滤，
    各厂管理员只能看到本厂日志）；未登录错误 company/user 为空，仅落库
    供排查，不进入任何公司的读取范围。
    """

    class Level(models.TextChoices):
        INFO = 'info', '信息'
        WARN = 'warn', '告警'
        ERROR = 'error', '错误'

    company = models.ForeignKey(
        'accounts.Company', on_delete=models.CASCADE, null=True, blank=True,
        related_name='client_logs', verbose_name='公司', db_index=True)
    user = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='client_logs', verbose_name='上报用户')
    level = models.CharField('级别', max_length=8, choices=Level.choices, default='info')
    page = models.CharField('页面路径', max_length=64, blank=True, default='')
    message = models.CharField('摘要', max_length=255)
    detail = models.TextField('详情（栈/参数）', blank=True, default='')

    class Meta:
        db_table = 'client_log'
        verbose_name = '客户端日志'
        verbose_name_plural = '客户端日志'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'user', 'created_at'], name='idx_clog_co_user_time'),
        ]

    def __str__(self):
        return f'[{self.level}] {self.message}'
