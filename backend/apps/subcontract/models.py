"""外发数据模型（GAP-5 轻量版）。

OutsourceOrder：外发单（发给外协厂的加工单）。
  - 供应商可用主档（warehouse.Supplier）或手填 factory_name（无主档外协厂）；
  - 加工费口径：amount = send_qty × unit_price（发出计价）；按收回合格量计费/
    发料三本账（SubcontractMaterial）留 P3 全量扩展；
  - 状态机：已发出 sent → 部分收回 partial → 已收回 returned → 已结算 settled。
OutsourceReceive：收回明细，逐次一条；received_qty 冗余进主单。
"""
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import BaseModel


class OutsourceOrder(BaseModel):
    """外发单。"""

    class Status(models.TextChoices):
        SENT = 'sent', '已发出'
        PARTIAL = 'partial', '部分收回'
        RETURNED = 'returned', '已收回'
        SETTLED = 'settled', '已结算'

    company = models.ForeignKey(
        'accounts.Company', models.CASCADE, related_name='outsource_orders', verbose_name='公司')
    no = models.CharField('外发单号', max_length=32)
    supplier = models.ForeignKey(
        'warehouse.Supplier', models.SET_NULL, null=True, blank=True,
        related_name='outsource_orders', verbose_name='外协厂(主档)')
    factory_name = models.CharField(
        '外协厂名称', max_length=128, blank=True, default='',
        help_text='无主档时手填；有主档时冗余快照')
    order = models.ForeignKey(
        'orders.Order', models.SET_NULL, null=True, blank=True,
        related_name='outsource_orders', verbose_name='关联订单')
    style = models.ForeignKey(
        'catalog.Style', models.SET_NULL, null=True, blank=True,
        related_name='outsource_orders', verbose_name='款式')
    process_desc = models.CharField('外发工序说明', max_length=200, blank=True, default='')
    send_qty = models.PositiveIntegerField('外发数量')
    received_qty = models.PositiveIntegerField('收回数量', default=0)
    unit_price = models.DecimalField('加工单价/件', max_digits=10, decimal_places=3, default=0)
    amount = models.DecimalField('加工费', max_digits=12, decimal_places=2, default=0)
    send_date = models.DateField('发出日期', default=timezone.localdate)
    due_date = models.DateField('交回日期', null=True, blank=True)
    return_date = models.DateField('收回日期', null=True, blank=True)
    status = models.CharField(
        '状态', max_length=16, choices=Status.choices, default=Status.SENT, db_index=True)
    remark = models.CharField('备注', max_length=200, blank=True, default='')
    creator = models.ForeignKey(
        'accounts.User', models.SET_NULL, null=True,
        related_name='outsource_orders_created', verbose_name='创建人')

    class Meta:
        db_table = 'outsource_order'
        verbose_name = '外发单'
        verbose_name_plural = verbose_name
        ordering = ['-send_date', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'no'], condition=Q(is_deleted=False),
                name='uq_outsource_company_no'),
        ]
        indexes = [
            models.Index(fields=['company', 'status', 'send_date'],
                         name='idx_outsource_company_status'),
            models.Index(fields=['company', 'supplier'], name='idx_outsource_company_supplier'),
        ]

    def __str__(self):
        return f'{self.no} {self.factory_name} x{self.send_qty}'


class OutsourceReceive(BaseModel):
    """收回明细：逐次一条，主单 received_qty/return_date/status 冗余同步。"""

    company = models.ForeignKey(
        'accounts.Company', models.CASCADE, related_name='outsource_receives', verbose_name='公司')
    order = models.ForeignKey(
        OutsourceOrder, models.CASCADE, related_name='receives', verbose_name='外发单')
    qty = models.PositiveIntegerField('收回数量')
    received_at = models.DateTimeField('收回时间', default=timezone.now)
    receiver = models.ForeignKey(
        'accounts.User', models.SET_NULL, null=True,
        related_name='outsource_receives', verbose_name='收货人')
    remark = models.CharField('备注', max_length=200, blank=True, default='')

    class Meta:
        db_table = 'outsource_receive'
        verbose_name = '外发收回'
        verbose_name_plural = verbose_name
        ordering = ['-received_at', '-id']

    def __str__(self):
        return f'{self.order_id} 收回 x{self.qty}'
