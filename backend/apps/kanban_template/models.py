"""看板模板模型：公司维度的生产看板列配置（表头列 + 每列绑定规则）。

设计文档：docs/kanban-template-design.md 第 3 节。
只新增表，不改任何现有业务表。
"""
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class KanbanTemplate(BaseModel):
    """看板模板（公司维度）。每公司可建多套，其中一套 is_default=默认看板。"""

    class Dataset(models.TextChoices):
        PLAN_ROWS = 'dashboard_plans', '生产计划表（款号+下单日期+颜色）'

    company = models.ForeignKey('accounts.Company', verbose_name='公司',
                                on_delete=models.CASCADE,
                                related_name='kanban_templates')
    name = models.CharField('模板名称', max_length=64)
    dataset = models.CharField('行数据源', max_length=32,
                               choices=Dataset.choices, default=Dataset.PLAN_ROWS)
    filters = models.JSONField('筛选条件集（声明式 JSON，预留）', default=dict, blank=True)
    is_default = models.BooleanField('默认模板', default=False)
    version = models.PositiveIntegerField('版本号', default=1)

    class Meta:
        db_table = 'kanban_template'
        verbose_name = '看板模板'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'name'], condition=Q(is_deleted=False),
                                    name='uq_kanban_template_company_name'),
        ]
        indexes = [
            models.Index(fields=['company', 'is_deleted'], name='idx_ktpl_company_deleted'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.name}'


class KanbanColumn(BaseModel):
    """模板列定义：一列 = 标题（复刻客户 Excel 表头）+ 绑定规则。

    bind_type:
      field   系统字段直取（field_key 必须在 fields 注册表内）
      formula 公式表达式（expr，simpleeval + AST 白名单求值，禁 eval）
      static  静态文本（static_text 原样输出）
    extra 展示配置（JSON）：
      merge     纵向合并：'block'=随款号块合并整块一行 / 'value'=块内连续同值合并 / 'none'
      group     两级表头父级（如「1组」）；小程序据此算表头 colspan/rowspan
      width     列宽 rpx（缺省 150）
      placeholder 无数据占位（缺省 '—'，todo 类字段必显占位）
      numfmt    'int'=整数（浮点去尾）
      link      下钻目标（P0）：'production'|'ticket'|'order'（白名单外渲染层忽略）
    """

    class BindType(models.TextChoices):
        FIELD = 'field', '系统字段'
        FORMULA = 'formula', '公式'
        STATIC = 'static', '静态文本'

    template = models.ForeignKey(KanbanTemplate, verbose_name='所属模板',
                                 on_delete=models.CASCADE, related_name='columns')
    seq = models.PositiveIntegerField('列序（从 0）')
    title = models.CharField('列标题', max_length=64)
    bind_type = models.CharField('绑定类型', max_length=16,
                                 choices=BindType.choices, default=BindType.FIELD)
    field_key = models.CharField('系统字段 key', max_length=64, blank=True, default='')
    expr = models.CharField('公式表达式', max_length=256, blank=True, default='')
    static_text = models.CharField('静态文本', max_length=64, blank=True, default='')
    extra = models.JSONField('展示配置', default=dict, blank=True)

    class Meta:
        db_table = 'kanban_column'
        verbose_name = '看板模板列'
        verbose_name_plural = verbose_name
        ordering = ['seq', 'id']

    def __str__(self):
        return f'{self.template_id}:{self.seq}:{self.title}'
