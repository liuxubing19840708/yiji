"""订单管理模型：客户订单（Order）+ 色码数量明细（OrderItem）。

数据模型与 DDL 严格按《系统重设计实施方案》第三部分（2026-10-20 已批准）：
  - 表 order / order_item；
  - 唯一约束一律带 condition=Q(is_deleted=False)（软删后允许同号/同色码复用）；
  - Order.company CASCADE、Order.customer SET_NULL（删客户不影响订单）、
    OrderItem.order CASCADE（订单软删由视图层负责，明细随单全量替换）。
"""
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class Order(BaseModel):
    """客户订单：一张单 = 客户 × 交期 × 颜色尺码数量矩阵。

    order_no 公司内唯一（软删防撞：condition 只约束未删除行）；
    留空时由视图层自动生成 SO+4 位顺延（all_objects 扫描防软删段复用混乱）。
    status 状态机：producing(生产中) → done(已完成) / cancelled(已取消)
    （<100% 允许完成，管理员知情操作；已挂床次不可删只可取消）。
    """

    class OrderType(models.TextChoices):
        SELF = '自产', '自产'
        OUTSOURCED = '外发', '外发'

    class Status(models.TextChoices):
        PRODUCING = 'producing', '生产中'
        DONE = 'done', '已完成'
        CANCELLED = 'cancelled', '已取消'

    company = models.ForeignKey('accounts.Company', verbose_name='公司',
                                on_delete=models.CASCADE, related_name='orders')
    order_no = models.CharField('订单号', max_length=32, db_index=True)
    customer = models.ForeignKey('catalog.Customer', verbose_name='客户',
                                 on_delete=models.SET_NULL, related_name='orders',
                                 null=True, blank=True)
    delivery_date = models.DateField('交期', null=True, blank=True,
                                     help_text='有交期才有交期灯（黄=距交期≤7天，红=过期未完成）')
    order_type = models.CharField('订单类型', max_length=16,
                                  choices=OrderType.choices, default=OrderType.SELF)
    status = models.CharField('订单状态', max_length=16,
                              choices=Status.choices, default=Status.PRODUCING)
    # 款式关联（2026-10-21 用户 8 项需求 #3）：存量订单兼容 null；
    # 挂单建床时 production.style 兜底取 order.style
    style = models.ForeignKey('catalog.Style', verbose_name='款式',
                              on_delete=models.SET_NULL, related_name='orders',
                              null=True, blank=True)
    # 备注图片（/media/ 相对路径数组）与订单关联工序 [{process_id, price}]
    images = models.JSONField('备注图片', default=list, blank=True)
    process_items = models.JSONField('订单工序', default=list, blank=True,
                                     help_text='[{process_id, price}]')
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'order'
        verbose_name = '客户订单'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'order_no'],
                                    condition=Q(is_deleted=False),
                                    name='uq_order_company_order_no'),
        ]
        indexes = [
            models.Index(fields=['company', 'status'], name='idx_order_company_status'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.order_no}'


class OrderItem(BaseModel):
    """订单色码明细：颜色 × 尺码 × 数量（同单同色码唯一，软删防撞）。"""

    order = models.ForeignKey(Order, verbose_name='订单',
                              on_delete=models.CASCADE, related_name='items')
    color = models.CharField('颜色', max_length=64)
    size = models.CharField('尺码', max_length=32)
    qty = models.PositiveIntegerField('订单数量')

    class Meta:
        db_table = 'order_item'
        verbose_name = '订单明细'
        verbose_name_plural = verbose_name
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['order', 'color', 'size'],
                                    condition=Q(is_deleted=False),
                                    name='uq_order_item_order_color_size'),
        ]

    def __str__(self):
        return f'{self.order_id}:{self.color}/{self.size}x{self.qty}'
