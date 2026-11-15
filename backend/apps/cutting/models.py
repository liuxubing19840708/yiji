"""裁片下发、签收与取消事实。

待签收单是预占事实而非库存余额；数量流水只能由 manufacturing 服务在签收时生成。
一行即一张单票交接单，保留节点、接收人及各状态时间用于明细追踪。
"""
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class CuttingHandover(BaseModel):
    class Status(models.TextChoices):
        PENDING = 'pending', '待签收'
        SIGNED = 'signed', '已签收'
        CANCELLED = 'cancelled', '已取消'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='cutting_handovers', verbose_name='公司')
    ticket = models.ForeignKey('catalog.Ticket', on_delete=models.PROTECT,
                               related_name='cutting_handovers', verbose_name='菲票')
    source_node = models.ForeignKey('manufacturing.RouteNode', on_delete=models.PROTECT,
                                    related_name='cutting_out_handovers', verbose_name='裁剪节点')
    target_node = models.ForeignKey('manufacturing.RouteNode', on_delete=models.PROTECT,
                                    related_name='cutting_in_handovers', verbose_name='目标节点')
    quantity = models.PositiveIntegerField('交接数量')
    receiver = models.ForeignKey('accounts.User', on_delete=models.PROTECT,
                                 related_name='cutting_receipts', verbose_name='指定接收人')
    request_id = models.CharField('建单幂等号', max_length=64)
    request_fingerprint = models.CharField('建单请求指纹', max_length=64)
    status = models.CharField('状态', max_length=16, choices=Status.choices,
                              default=Status.PENDING, db_index=True)
    remark = models.CharField('备注', max_length=255, blank=True, default='')
    created_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT,
                                   related_name='cutting_created_handovers', verbose_name='下发人')
    signed_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, null=True, blank=True,
                                  related_name='cutting_signed_handovers', verbose_name='签收操作人')
    signed_at = models.DateTimeField('签收时间', null=True, blank=True)
    cancelled_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT,
                                     null=True, blank=True,
                                     related_name='cutting_cancelled_handovers', verbose_name='取消人')
    cancelled_at = models.DateTimeField('取消时间', null=True, blank=True)
    outgoing_ledger = models.OneToOneField('manufacturing.QuantityLedger', on_delete=models.PROTECT,
                                           null=True, blank=True, related_name='cutting_out_handover',
                                           verbose_name='来源转出流水')
    incoming_ledger = models.OneToOneField('manufacturing.QuantityLedger', on_delete=models.PROTECT,
                                           null=True, blank=True, related_name='cutting_in_handover',
                                           verbose_name='目标接收流水')

    class Meta:
        db_table = 'cutting_handover'
        ordering = ['-created_at', '-id']
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name='ck_cutting_handover_qty'),
            models.UniqueConstraint(fields=['company', 'request_id'],
                                    name='uq_cutting_handover_request'),
            models.CheckConstraint(
                condition=(Q(status='pending', signed_at__isnull=True, cancelled_at__isnull=True)
                           | Q(status='signed', signed_at__isnull=False,
                               outgoing_ledger__isnull=False, incoming_ledger__isnull=False,
                               cancelled_at__isnull=True)
                           | Q(status='cancelled', cancelled_at__isnull=False,
                               signed_at__isnull=True, outgoing_ledger__isnull=True,
                               incoming_ledger__isnull=True)),
                name='ck_cutting_handover_state'),
        ]
        indexes = [
            models.Index(fields=['company', 'ticket', 'source_node', 'status'],
                         name='idx_cutting_reservation'),
            models.Index(fields=['company', 'receiver', 'status'], name='idx_cutting_receiver'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.ticket_id}:{self.quantity}:{self.status}'
