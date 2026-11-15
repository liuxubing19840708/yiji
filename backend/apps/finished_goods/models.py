"""包装箱与成品入库单证。

这里只保存箱/单证明细和状态，不保存可修改的库存余额。数量事实源始终是
manufacturing.QuantityLedger；现货只读 finished_inbound 节点净 available。
"""
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import BaseModel
from .exceptions import FrozenError


class DocumentStatus(models.TextChoices):
    DRAFT = 'draft', '待审核'
    APPROVED = 'approved', '已审核'
    REVERSED = 'reversed', '已冲销'


class PackingTask(BaseModel):
    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='packing_tasks', verbose_name='公司')
    task_no = models.CharField('包装任务号', max_length=64)
    route = models.ForeignKey('manufacturing.ProductionRoute', on_delete=models.PROTECT,
                              related_name='packing_tasks', verbose_name='生产路线')
    packing_node = models.ForeignKey('manufacturing.RouteNode', on_delete=models.PROTECT,
                                     related_name='finished_packing_tasks', verbose_name='包装节点')
    inbound_node = models.ForeignKey('manufacturing.RouteNode', on_delete=models.PROTECT,
                                     related_name='packing_tasks', verbose_name='成品入库节点')
    status = models.CharField('状态', max_length=16, choices=DocumentStatus.choices,
                              default=DocumentStatus.DRAFT, db_index=True)
    request_id = models.CharField('建单幂等号', max_length=64)
    request_fingerprint = models.CharField('建单请求指纹', max_length=64)
    approval_request_id = models.CharField('审核幂等号', max_length=64, blank=True, default='')
    remark = models.CharField('备注', max_length=255, blank=True, default='')
    creator = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='packing_tasks_created', verbose_name='创建人')
    approver = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='packing_tasks_approved', verbose_name='审核人')
    approved_at = models.DateTimeField('审核时间', null=True, blank=True)
    reversal_request_id = models.CharField('冲销幂等号', max_length=64, blank=True, default='')
    reversal_reason = models.CharField('冲销原因', max_length=255, blank=True, default='')
    reverser = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='+', verbose_name='冲销人')
    reversed_at = models.DateTimeField('冲销时间', null=True, blank=True)

    class Meta:
        db_table = 'finished_packing_task'
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'task_no'], condition=Q(is_deleted=False),
                                    name='uq_fg_task_company_no'),
            models.UniqueConstraint(fields=['company', 'request_id'], name='uq_fg_task_request'),
            models.UniqueConstraint(fields=['company', 'approval_request_id'],
                                    condition=~Q(approval_request_id=''),
                                    name='uq_fg_task_approval_request'),
            models.UniqueConstraint(fields=['company', 'reversal_request_id'],
                                    condition=~Q(reversal_request_id=''),
                                    name='uq_fg_task_reversal_request'),
        ]
        indexes = [models.Index(fields=['company', 'route', 'status'],
                                name='idx_fg_task_co_route_status')]


class PackingBox(BaseModel):
    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='packing_boxes', verbose_name='公司')
    task = models.ForeignKey(PackingTask, on_delete=models.PROTECT,
                             related_name='boxes', verbose_name='包装任务')
    box_no = models.CharField('箱号', max_length=64)

    class Meta:
        db_table = 'finished_packing_box'
        ordering = ['id']
        constraints = [
            # 不使用软删除条件：箱号一经使用，在公司内永久不可复用。
            models.UniqueConstraint(fields=['company', 'box_no'], name='uq_fg_box_company_no'),
        ]

    def save(self, *args, **kwargs):
        if self.task_id and self.task.status == DocumentStatus.APPROVED:
            raise FrozenError('包装已审核，箱及箱内明细不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.task_id and self.task.status == DocumentStatus.APPROVED:
            raise FrozenError('包装已审核，箱及箱内明细不可删除')
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f'{self.company_id}:{self.box_no}'


class PackingBoxLine(BaseModel):
    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='packing_box_lines', verbose_name='公司')
    box = models.ForeignKey(PackingBox, on_delete=models.PROTECT,
                            related_name='lines', verbose_name='包装箱')
    ticket = models.ForeignKey('catalog.Ticket', on_delete=models.PROTECT,
                               related_name='packing_box_lines', verbose_name='菲票')
    color = models.CharField('颜色快照', max_length=64, blank=True, default='')
    size = models.CharField('尺码快照', max_length=32, blank=True, default='')
    quantity = models.PositiveIntegerField('数量')

    class Meta:
        db_table = 'finished_packing_box_line'
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['box', 'ticket'], name='uq_fg_box_line_ticket'),
            models.CheckConstraint(condition=Q(quantity__gt=0), name='ck_fg_box_line_qty'),
        ]

    def save(self, *args, **kwargs):
        if self.box_id and self.box.task.status == DocumentStatus.APPROVED:
            raise FrozenError('包装已审核，箱内明细不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.box_id and self.box.task.status == DocumentStatus.APPROVED:
            raise FrozenError('包装已审核，箱内明细不可删除')
        return super().delete(*args, **kwargs)


class FinishedInbound(BaseModel):
    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='finished_inbounds', verbose_name='公司')
    document_no = models.CharField('成品入库单号', max_length=64)
    route = models.ForeignKey('manufacturing.ProductionRoute', on_delete=models.PROTECT,
                              related_name='finished_inbounds', verbose_name='生产路线')
    packing_node = models.ForeignKey('manufacturing.RouteNode', on_delete=models.PROTECT,
                                     related_name='finished_outbounds', verbose_name='包装节点')
    inbound_node = models.ForeignKey('manufacturing.RouteNode', on_delete=models.PROTECT,
                                     related_name='finished_inbound_documents', verbose_name='成品入库节点')
    warehouse_name = models.CharField('仓库快照', max_length=128, blank=True, default='')
    location_name = models.CharField('库位快照', max_length=128, blank=True, default='')
    status = models.CharField('状态', max_length=16, choices=DocumentStatus.choices,
                              default=DocumentStatus.DRAFT, db_index=True)
    request_id = models.CharField('建单幂等号', max_length=64)
    request_fingerprint = models.CharField('建单请求指纹', max_length=64)
    approval_request_id = models.CharField('审核幂等号', max_length=64, blank=True, default='')
    remark = models.CharField('备注', max_length=255, blank=True, default='')
    creator = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='finished_inbounds_created', verbose_name='创建人')
    approver = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='finished_inbounds_approved', verbose_name='审核人')
    approved_at = models.DateTimeField('审核时间', null=True, blank=True)
    reversal_request_id = models.CharField('冲销幂等号', max_length=64, blank=True, default='')
    reversal_reason = models.CharField('冲销原因', max_length=255, blank=True, default='')
    reverser = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='+', verbose_name='冲销人')
    reversed_at = models.DateTimeField('冲销时间', null=True, blank=True)

    class Meta:
        db_table = 'finished_inbound'
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'document_no'], condition=Q(is_deleted=False),
                                    name='uq_fg_inbound_company_no'),
            models.UniqueConstraint(fields=['company', 'request_id'], name='uq_fg_inbound_request'),
            models.UniqueConstraint(fields=['company', 'approval_request_id'],
                                    condition=~Q(approval_request_id=''),
                                    name='uq_fg_inbound_approval_request'),
            models.UniqueConstraint(fields=['company', 'reversal_request_id'],
                                    condition=~Q(reversal_request_id=''),
                                    name='uq_fg_inbound_reversal_request'),
        ]
        indexes = [models.Index(fields=['company', 'route', 'status'],
                                name='idx_fg_in_co_route_status')]


class FinishedInboundLine(BaseModel):
    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='finished_inbound_lines', verbose_name='公司')
    document = models.ForeignKey(FinishedInbound, on_delete=models.PROTECT,
                                 related_name='lines', verbose_name='成品入库单')
    box = models.ForeignKey(PackingBox, on_delete=models.PROTECT,
                            related_name='inbound_lines', verbose_name='包装箱')
    ticket = models.ForeignKey('catalog.Ticket', on_delete=models.PROTECT,
                               related_name='finished_inbound_lines', verbose_name='菲票')
    color = models.CharField('颜色快照', max_length=64, blank=True, default='')
    size = models.CharField('尺码快照', max_length=32, blank=True, default='')
    quantity = models.PositiveIntegerField('数量')

    class Meta:
        db_table = 'finished_inbound_line'
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['document', 'box', 'ticket'],
                                    name='uq_fg_in_line_doc_box_ticket'),
            models.CheckConstraint(condition=Q(quantity__gt=0), name='ck_fg_in_line_qty'),
        ]

    def save(self, *args, **kwargs):
        if self.document_id and self.document.status == DocumentStatus.APPROVED:
            raise FrozenError('入库单已审核，入库明细不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.document_id and self.document.status == DocumentStatus.APPROVED:
            raise FrozenError('入库单已审核，入库明细不可删除')
        return super().delete(*args, **kwargs)


def mark_approved(document, *, operator, request_id):
    """仅供本模块服务在同事务内完成状态收口。"""
    document.status = DocumentStatus.APPROVED
    document.approval_request_id = request_id
    document.approver = operator
    document.approved_at = timezone.now()
    document.save(update_fields=['status', 'approval_request_id', 'approver', 'approved_at',
                                 'updated_at'])
    return document


def mark_reversed(document, *, operator, request_id, reason):
    """冲销只改变单证状态，原明细与数量流水均保留。"""
    document.status = DocumentStatus.REVERSED
    document.reversal_request_id = request_id
    document.reversal_reason = reason
    document.reverser = operator
    document.reversed_at = timezone.now()
    document.save(update_fields=['status', 'reversal_request_id', 'reversal_reason', 'reverser',
                                 'reversed_at', 'updated_at'])
    return document
