"""通用交接事实、明细、事件及幂等请求。

裁片交接在待接收时预占来源可转量，接收后由明细关联双方数量流水。
batch_no 只是查询汇总键；HandoverRequest 是技术幂等记录，不是批次业务主单。
"""
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel
from .exceptions import ImmutableEventError


class HandoverKind(models.TextChoices):
    CUTTING = 'cutting', '裁片'


class HandoverPolicy(BaseModel):
    class Mode(models.TextChoices):
        OFF = 'off', '关闭'
        WARN = 'warn', '提醒'
        STRICT = 'strict', '严格（由业务适配层执行）'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='handover_policies', verbose_name='公司')
    kind = models.CharField('交接类型', max_length=16, choices=HandoverKind.choices,
                            default=HandoverKind.CUTTING)
    mode = models.CharField('门禁模式', max_length=8, choices=Mode.choices, default=Mode.OFF)
    source_node = models.ForeignKey('manufacturing.RouteNode', on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='handover_source_policies',
                                    verbose_name='来源节点')
    target_node = models.ForeignKey('manufacturing.RouteNode', on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='handover_target_policies',
                                    verbose_name='目标节点')
    target_group_no = models.PositiveIntegerField('目标组编号', null=True, blank=True)
    receiver = models.ForeignKey('accounts.User', on_delete=models.PROTECT, null=True, blank=True,
                                 related_name='handover_receiver_policies', verbose_name='指定接收人')
    timeout_minutes = models.PositiveIntegerField('超时分钟数', default=120)
    updated_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='updated_handover_policies', verbose_name='更新人')

    class Meta:
        db_table = 'handover_policy'
        ordering = ['id']
        constraints = [
            # SQLite/PostgreSQL 均支持部分唯一索引；四种空值组合分别约束，
            # 避免普通唯一约束把多个 NULL 当成互不冲突而放入重复默认策略。
            models.UniqueConstraint(
                fields=['company', 'kind'], name='uq_handover_policy_default',
                condition=Q(is_deleted=False, source_node__isnull=True,
                            target_node__isnull=True)),
            models.UniqueConstraint(
                fields=['company', 'kind', 'source_node'], name='uq_handover_policy_source',
                condition=Q(is_deleted=False, source_node__isnull=False,
                            target_node__isnull=True)),
            models.UniqueConstraint(
                fields=['company', 'kind', 'target_node'], name='uq_handover_policy_target',
                condition=Q(is_deleted=False, source_node__isnull=True,
                            target_node__isnull=False)),
            models.UniqueConstraint(
                fields=['company', 'kind', 'source_node', 'target_node'],
                name='uq_handover_policy_pair',
                condition=Q(is_deleted=False, source_node__isnull=False,
                            target_node__isnull=False)),
            models.CheckConstraint(condition=Q(timeout_minutes__gt=0),
                                   name='ck_handover_policy_timeout'),
        ]


class Handover(BaseModel):
    class Status(models.TextChoices):
        PENDING = 'pending', '待接收'
        RECEIVED = 'received', '已接收'
        REJECTED = 'rejected', '已拒收'
        CANCELLED = 'cancelled', '已取消'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='handovers', verbose_name='公司')
    handover_no = models.CharField('交接单号', max_length=64)
    batch_no = models.CharField('批量号', max_length=64, db_index=True)
    kind = models.CharField('交接类型', max_length=16, choices=HandoverKind.choices)
    status = models.CharField('状态', max_length=16, choices=Status.choices,
                              default=Status.PENDING, db_index=True)
    source_node = models.ForeignKey('manufacturing.RouteNode', on_delete=models.PROTECT,
                                    related_name='outgoing_handovers', verbose_name='来源节点')
    target_node = models.ForeignKey('manufacturing.RouteNode', on_delete=models.PROTECT,
                                    related_name='incoming_handovers', verbose_name='目标节点')
    target_group_no = models.PositiveIntegerField('目标组编号', null=True, blank=True)
    receiver = models.ForeignKey('accounts.User', on_delete=models.PROTECT, null=True, blank=True,
                                 related_name='assigned_handovers', verbose_name='指定接收人')
    created_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT,
                                   related_name='created_handovers', verbose_name='交出人')
    received_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='received_handovers', verbose_name='实际接收人')
    received_at = models.DateTimeField('接收时间', null=True, blank=True)
    reject_reason = models.CharField('拒收原因', max_length=255, blank=True, default='')
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'handover'
        ordering = ['-created_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'handover_no'],
                                    name='uq_handover_company_no'),
            models.CheckConstraint(
                condition=Q(receiver__isnull=False) | Q(target_group_no__isnull=False),
                name='ck_handover_recipient'),
            models.CheckConstraint(
                condition=(Q(status='received', received_by__isnull=False,
                             received_at__isnull=False)
                           | (~Q(status='received') & Q(received_by__isnull=True,
                                                       received_at__isnull=True))),
                name='ck_handover_receipt_state'),
        ]
        indexes = [
            models.Index(fields=['company', 'batch_no'], name='idx_handover_co_batch'),
            models.Index(fields=['company', 'receiver', 'status'],
                         name='idx_handover_co_receiver'),
            models.Index(fields=['company', 'created_by', 'status'],
                         name='idx_handover_co_creator'),
        ]

    @property
    def total_quantity(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def received_quantity(self):
        return sum(item.received_quantity for item in self.items.all())

    @property
    def has_discrepancy(self):
        return any(item.received_quantity != item.quantity or bool(item.difference_type)
                   for item in self.items.all())

    def __str__(self):
        return f'{self.company_id}:{self.handover_no}:{self.status}'


class HandoverItem(BaseModel):
    class DifferenceType(models.TextChoices):
        SHORTAGE = 'shortage', '短少'
        OVERAGE = 'overage', '多收'
        DAMAGE = 'damage', '破损'
        WRONG_ITEM = 'wrong_item', '错货'
        QUALITY = 'quality', '质量问题'
        OTHER = 'other', '其他'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='handover_items', verbose_name='公司')
    handover = models.ForeignKey(Handover, on_delete=models.PROTECT,
                                 related_name='items', verbose_name='交接单')
    ticket = models.ForeignKey('catalog.Ticket', on_delete=models.PROTECT,
                               related_name='handover_items', verbose_name='菲票')
    quantity = models.PositiveIntegerField('交接数量')
    received_quantity = models.PositiveIntegerField('实收数量', default=0)
    difference_type = models.CharField('差异类型', max_length=16,
                                       choices=DifferenceType.choices, blank=True, default='')
    reason = models.CharField('差异原因', max_length=255, blank=True, default='')
    out_ledger = models.OneToOneField('manufacturing.QuantityLedger', on_delete=models.PROTECT,
                                      null=True, blank=True, related_name='handover_out_item',
                                      verbose_name='来源实转流水')
    in_ledger = models.OneToOneField('manufacturing.QuantityLedger', on_delete=models.PROTECT,
                                     null=True, blank=True, related_name='handover_in_item',
                                     verbose_name='目标实收流水')

    class Meta:
        db_table = 'handover_item'
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['handover', 'ticket'], name='uq_handover_item_ticket'),
            models.CheckConstraint(condition=Q(quantity__gt=0), name='ck_handover_item_qty'),
        ]
        indexes = [models.Index(fields=['company', 'ticket'], name='idx_handover_item_co_ticket')]


class HandoverEvent(models.Model):
    """只增不改的交接事件；actor 是操作账号，actual_user 记录实际责任人。"""

    class Action(models.TextChoices):
        CREATED = 'created', '创建'
        RECEIVED = 'received', '接收'
        REJECTED = 'rejected', '拒收'
        REASSIGNED = 'reassigned', '改派'
        CANCELLED = 'cancelled', '取消'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='handover_events', verbose_name='公司')
    handover = models.ForeignKey(Handover, on_delete=models.PROTECT,
                                 related_name='events', verbose_name='交接单')
    action = models.CharField('动作', max_length=16, choices=Action.choices)
    actor = models.ForeignKey('accounts.User', on_delete=models.PROTECT,
                              related_name='handover_events', verbose_name='操作账号')
    actual_user = models.ForeignKey('accounts.User', on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='actual_handover_events', verbose_name='实际责任人')
    reason = models.CharField('原因', max_length=255, blank=True, default='')
    before = models.JSONField('变更前', default=dict, blank=True)
    after = models.JSONField('变更后', default=dict, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'handover_event'
        ordering = ['created_at', 'id']
        indexes = [models.Index(fields=['company', 'handover', 'created_at'],
                                name='idx_handover_event_co_time')]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ImmutableEventError('交接事件不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableEventError('交接事件不可删除')


class HandoverRequest(models.Model):
    """公司内永久唯一的技术幂等键，可指向批次或单张交接。"""

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='handover_requests', verbose_name='公司')
    request_id = models.CharField('幂等请求号', max_length=64)
    request_fingerprint = models.CharField('请求指纹', max_length=64)
    action = models.CharField('动作', max_length=16)
    batch_no = models.CharField('批量号', max_length=64, blank=True, default='')
    handover = models.ForeignKey(Handover, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name='idempotency_requests', verbose_name='交接单')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'handover_request'
        constraints = [models.UniqueConstraint(fields=['company', 'request_id'],
                                               name='uq_handover_company_request')]
        indexes = [models.Index(fields=['company', 'batch_no'], name='idx_handover_req_co_batch')]
