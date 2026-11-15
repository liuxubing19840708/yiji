"""生产路线与不可变数量流水。

流水才是数量事实源，StageExecution 只是操作事件，不与全工序 Record 求和混用。
"""
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel
from .exceptions import ImmutableLedgerError


class ProductionStage(BaseModel):
    """公司内可复用的标准生产节点。"""

    class StageType(models.TextChoices):
        CUTTING = 'cutting', '裁剪'
        SEWING = 'sewing', '车缝'
        SEMI_INSPECTION = 'semi_inspection', '半成品检验'
        SPECIAL_MACHINE = 'special_machine', '专机'
        IRONING = 'ironing', '整烫'
        FINAL_INSPECTION = 'final_inspection', '成品检验'
        PACKING = 'packing', '包装'
        FINISHED_INBOUND = 'finished_inbound', '成品入库'
        OTHER = 'other', '其他'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='manufacturing_stages', verbose_name='公司')
    code = models.CharField('节点编码', max_length=32)
    name = models.CharField('节点名称', max_length=64)
    stage_type = models.CharField('节点类型', max_length=32, choices=StageType.choices)
    is_active = models.BooleanField('是否启用', default=True, db_index=True)

    class Meta:
        db_table = 'manufacturing_stage'
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'code'],
                                    condition=Q(is_deleted=False),
                                    name='uq_mfg_stage_company_code'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.code}'


class ProductionRoute(BaseModel):
    """路线模板或订单/床次路线实例，实例必须且只能挂一个业务锚点。"""

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='manufacturing_routes', verbose_name='公司')
    code = models.CharField('路线编码', max_length=64)
    name = models.CharField('路线名称', max_length=64)
    is_template = models.BooleanField('是否模板', default=False)
    order = models.OneToOneField('orders.Order', on_delete=models.CASCADE, null=True, blank=True,
                                 related_name='manufacturing_route', verbose_name='客户订单')
    production = models.OneToOneField('production.Production', on_delete=models.CASCADE,
                                      null=True, blank=True,
                                      related_name='manufacturing_route', verbose_name='床次')
    source_template = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True,
                                        related_name='instances', verbose_name='来源模板')
    previous_version = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True,
                                         related_name='next_versions', verbose_name='上一版本')
    version = models.PositiveIntegerField('模板版本', default=1)
    is_active = models.BooleanField('是否启用', default=True, db_index=True)

    class Meta:
        db_table = 'manufacturing_route'
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'code'],
                                    condition=Q(is_deleted=False),
                                    name='uq_mfg_route_company_code'),
            models.CheckConstraint(
                condition=(Q(is_template=True, order__isnull=True, production__isnull=True)
                           | Q(is_template=False, order__isnull=False, production__isnull=True)
                           | Q(is_template=False, order__isnull=True, production__isnull=False)),
                name='ck_mfg_route_anchor'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.code}'


class RouteNode(BaseModel):
    """路线节点快照。

    allow_parallel=True 表示本节点可与前一个节点共享同一前驱；连续 True 构成并行组。
    required=True 且 allow_skip=False 的节点不得被跨序跳过。
    """

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='manufacturing_route_nodes', verbose_name='公司')
    route = models.ForeignKey(ProductionRoute, on_delete=models.CASCADE,
                              related_name='nodes', verbose_name='路线')
    stage = models.ForeignKey(ProductionStage, on_delete=models.PROTECT,
                              related_name='route_nodes', verbose_name='生产节点')
    stage_code = models.CharField('节点编码快照', max_length=32, blank=True, default='')
    stage_name = models.CharField('节点名称快照', max_length=64, blank=True, default='')
    stage_type = models.CharField('节点类型快照', max_length=32,
                                  choices=ProductionStage.StageType.choices,
                                  default=ProductionStage.StageType.OTHER)
    sequence = models.PositiveIntegerField('顺序')
    required = models.BooleanField('必过', default=True)
    allow_parallel = models.BooleanField('可并行', default=False)
    allow_skip = models.BooleanField('可跳过', default=False)

    class Meta:
        db_table = 'manufacturing_route_node'
        ordering = ['sequence', 'id']
        constraints = [
            models.UniqueConstraint(fields=['route', 'sequence'],
                                    condition=Q(is_deleted=False),
                                    name='uq_mfg_node_route_sequence'),
        ]
        indexes = [
            models.Index(fields=['company', 'route'], name='idx_mfg_node_co_route'),
        ]

    def __str__(self):
        return f'{self.route_id}:{self.sequence}:{self.stage_id}'


class QuantityLedger(models.Model):
    """正数、不可变、公司隔离的数量事件。

    received/rework 是入站；transferred/scrapped 是出站；qualified 仅是质量里程碑，
    不扣库存。reversal 由服务按 reversal_of 原事件取反，绝不修改原流水。
    """

    class Event(models.TextChoices):
        RECEIVED = 'received', '接收'
        QUALIFIED = 'qualified', '合格'
        REWORK = 'rework', '返修入站'
        SCRAPPED = 'scrapped', '报废'
        TRANSFERRED = 'transferred', '转出'
        REVERSAL = 'reversal', '冲销'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='manufacturing_ledgers', verbose_name='公司')
    route = models.ForeignKey(ProductionRoute, on_delete=models.PROTECT,
                              related_name='ledger_entries', verbose_name='路线')
    node = models.ForeignKey(RouteNode, on_delete=models.PROTECT,
                             related_name='ledger_entries', verbose_name='节点')
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, null=True, blank=True,
                              related_name='manufacturing_ledgers', verbose_name='客户订单')
    production = models.ForeignKey('production.Production', on_delete=models.PROTECT,
                                   related_name='manufacturing_ledgers', verbose_name='床次')
    ticket = models.ForeignKey('catalog.Ticket', on_delete=models.PROTECT,
                               related_name='manufacturing_ledgers', verbose_name='菲票')
    color = models.CharField('颜色快照', max_length=64, blank=True, default='')
    size = models.CharField('尺码快照', max_length=32, blank=True, default='')
    event_type = models.CharField('事件', max_length=16, choices=Event.choices)
    quantity = models.PositiveIntegerField('数量（恒正）')
    source = models.CharField('来源', max_length=128, blank=True, default='',
                              help_text='外部单据/设备/导入批次等来源标识')
    operator = models.ForeignKey('accounts.User', on_delete=models.SET_NULL,
                                 null=True, blank=True,
                                 related_name='manufacturing_operations', verbose_name='操作人')
    related_entry = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True,
                                      related_name='linked_entries', verbose_name='关联流水')
    reversal_of = models.OneToOneField('self', on_delete=models.PROTECT, null=True, blank=True,
                                       related_name='reversal', verbose_name='被冲销流水')
    request_id = models.CharField('幂等请求号', max_length=64)
    request_fingerprint = models.CharField('请求指纹', max_length=64)
    reason = models.CharField('原因/备注', max_length=255, blank=True, default='')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'manufacturing_quantity_ledger'
        ordering = ['id']
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name='ck_mfg_ledger_qty_positive'),
            models.UniqueConstraint(fields=['company', 'request_id'],
                                    name='uq_mfg_ledger_request'),
            models.CheckConstraint(
                condition=(Q(event_type='reversal', reversal_of__isnull=False)
                           | ~Q(event_type='reversal') & Q(reversal_of__isnull=True)),
                name='ck_mfg_ledger_reversal'),
        ]
        indexes = [
            models.Index(fields=['company', 'ticket', 'node'], name='idx_mfg_ledger_balance'),
            models.Index(fields=['company', 'production', 'node'], name='idx_mfg_ledger_bed_node'),
            models.Index(fields=['company', 'order'], name='idx_mfg_ledger_order'),
            models.Index(fields=['company', 'created_at'], name='idx_mfg_ledger_co_time'),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ImmutableLedgerError('数量流水不可修改，请使用 reverse_quantity 冲销')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableLedgerError('数量流水不可删除，请使用 reverse_quantity 冲销')

    def __str__(self):
        return f'{self.company_id}:{self.ticket_id}:{self.node_id}:{self.event_type}:{self.quantity}'


class StageExecution(models.Model):
    """菲票在节点上的接收/完工/退回等执行事件，由服务与流水同事务写入。"""

    class Action(models.TextChoices):
        RECEIVE = 'receive', '接收'
        COMPLETE = 'complete', '完工'
        RETURN = 'return', '退回'
        TRANSFER = 'transfer', '转出'
        SCRAP = 'scrap', '报废'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='manufacturing_executions', verbose_name='公司')
    ticket = models.ForeignKey('catalog.Ticket', on_delete=models.PROTECT,
                               related_name='manufacturing_executions', verbose_name='菲票')
    node = models.ForeignKey(RouteNode, on_delete=models.PROTECT,
                             related_name='executions', verbose_name='节点')
    action = models.CharField('动作', max_length=16, choices=Action.choices)
    quantity = models.PositiveIntegerField('数量')
    ledger = models.OneToOneField(QuantityLedger, on_delete=models.PROTECT,
                                  related_name='execution', verbose_name='数量流水')
    operator = models.ForeignKey('accounts.User', on_delete=models.SET_NULL,
                                 null=True, blank=True,
                                 related_name='manufacturing_executions', verbose_name='操作人')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'manufacturing_stage_execution'
        ordering = ['id']
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name='ck_mfg_execution_qty_positive'),
        ]
        indexes = [
            models.Index(fields=['company', 'ticket', 'node'], name='idx_mfg_exec_ticket_node'),
        ]
