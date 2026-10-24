"""生产管理模型：床次订单、订单工序。"""
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class Production(BaseModel):
    """床次订单：一次裁货 = 一个床次，含若干菲票（扎）。

    锁住：锁定后不可再扫菲登记（防错登 / 已结款订单），锁定需管理员权限。
    裁完：= 裁剪完成（2026-10-18 拍板语义，非整单完结）。仅作状态标记：
         不拦截后道工序登记，也不拦截编菲加扎；工作台/看板以 is_cut_done=False
         视为「进行中」。人工在床次详情标记/取消。
    plan_qty：计划裁货数（建床时录入，与实裁 total_qty 分账；已裁≠计划仅数据可查）。
    分货模式（split_mode）已于 2026-10-18 拍板彻底删除（此前为无消费方的死配置）。
    """

    class OrderType(models.TextChoices):
        SELF = '自产', '自产'
        OUTSOURCED = '外发', '外发'

    company = models.ForeignKey('accounts.Company', verbose_name='公司',
                                on_delete=models.CASCADE, related_name='productions')
    style = models.ForeignKey('catalog.Style', verbose_name='款式',
                              on_delete=models.PROTECT, related_name='productions')
    bed_no = models.CharField('床次号', max_length=32)
    total_qty = models.PositiveIntegerField('裁货总数量', default=0)
    plan_qty = models.PositiveIntegerField(
        '计划裁货数量', null=True, blank=True,
        help_text='建床时录入的计划数；与实裁 total_qty 分账，不等时仅数据可查')
    order_type = models.CharField('订单类型', max_length=16,
                                  choices=OrderType.choices, default=OrderType.SELF)
    customer = models.ForeignKey('catalog.Customer', verbose_name='客户',
                                 on_delete=models.SET_NULL, related_name='productions',
                                 null=True, blank=True)
    # 挂单（2026-10-20 P1）：床次可挂客户订单；可空不回填（存量床次不回填），
    # source_order_no 语义保留为外部单号（挂单时自动带出订单号）
    order = models.ForeignKey('orders.Order', verbose_name='客户订单',
                              on_delete=models.SET_NULL, related_name='productions',
                              null=True, blank=True)
    fabric = models.CharField('面料', max_length=64, blank=True, default='')
    source_order_no = models.CharField(
        '来源单号/客户订单号', max_length=64, blank=True, default='', db_index=True,
        help_text='追溯锚点：客户投诉凭此定位床次（质量追溯 P0，手填不建客户订单模型）')
    is_locked = models.BooleanField('是否锁定', default=False)
    is_cut_done = models.BooleanField('是否裁完', default=False)

    class Meta:
        db_table = 'production'
        verbose_name = '床次订单'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'bed_no'], condition=Q(is_deleted=False),
                                    name='uq_production_company_bed'),
        ]
        indexes = [
            models.Index(fields=['company', 'style'], name='idx_production_company_style'),
            models.Index(fields=['company', 'order'], name='idx_production_company_order'),
        ]

    def __str__(self):
        return f'{self.company_id}:床次{self.bed_no}'


class ProductionProcess(BaseModel):
    """订单工序：配置该床次包含的工序及单价（可覆盖款式默认工序）。"""

    production = models.ForeignKey(Production, verbose_name='床次订单',
                                   on_delete=models.CASCADE, related_name='order_processes')
    process = models.ForeignKey('catalog.Process', verbose_name='工序',
                                on_delete=models.CASCADE, related_name='production_processes')
    price = models.DecimalField('工序单价', max_digits=10, decimal_places=3, default=0)

    class Meta:
        db_table = 'production_process'
        verbose_name = '订单工序'
        verbose_name_plural = verbose_name
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['production', 'process'], condition=Q(is_deleted=False),
                                    name='uq_production_process'),
        ]

    def __str__(self):
        return f'{self.production_id}:{self.process_id}'
