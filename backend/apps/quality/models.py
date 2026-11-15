"""质检数据模型（GAP-1）。

QualityRecord：一张菲票（可指定工序）的返修记录。
状态流转：返修中 rework → 已收回 received → 已完成 done。
数量口径：defect_qty 返修数量（≤ 扎数）；received_qty 收回数量（≤ defect_qty）。
质检人 = 创建时的操作人快照；producer 为返修工人（可空，车间送检后补指定）。
"""
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.core.models import BaseModel


class QualityRecord(BaseModel):
    """质检返修记录。"""

    class Status(models.TextChoices):
        REWORK = 'rework', '返修中'
        RECEIVED = 'received', '已收回'
        DONE = 'done', '已完成'

    company = models.ForeignKey(
        'accounts.Company', models.CASCADE, related_name='quality_records', verbose_name='公司')
    ticket = models.ForeignKey(
        'catalog.Ticket', models.CASCADE, related_name='quality_records', verbose_name='菲票')
    process = models.ForeignKey(
        'catalog.Process', models.SET_NULL, null=True, blank=True,
        related_name='quality_records', verbose_name='返修工序',
        help_text='空=整扎通用返修（不挂具体工序）')
    defect_qty = models.PositiveIntegerField('返修数量')
    reason = models.CharField('返修原因', max_length=200)
    status = models.CharField(
        '状态', max_length=16, choices=Status.choices,
        default=Status.REWORK, db_index=True)
    received_qty = models.PositiveIntegerField('收回数量', default=0)
    received_at = models.DateTimeField('收回时间', null=True, blank=True)
    producer = models.ForeignKey(
        'accounts.User', models.SET_NULL, null=True, blank=True,
        related_name='quality_repairs', verbose_name='返修工人')
    inspector = models.ForeignKey(
        'accounts.User', models.SET_NULL, null=True,
        related_name='quality_inspections', verbose_name='质检人')
    inspected_at = models.DateTimeField('质检时间', default=timezone.now)
    remark = models.CharField('备注', max_length=200, blank=True, default='')

    class Meta:
        db_table = 'quality_record'
        verbose_name = '质检返修'
        verbose_name_plural = verbose_name
        ordering = ['-inspected_at', '-id']
        constraints = [
            # 未收回/未完成的返修中记录：同菲票同工序只允许一条在途
            models.UniqueConstraint(
                fields=['company', 'ticket', 'process'], condition=Q(is_deleted=False, status='rework'),
                name='uq_quality_rework_ticket_process'),
        ]
        indexes = [
            models.Index(fields=['company', 'status', 'inspected_at'],
                         name='idx_quality_company_status'),
            models.Index(fields=['company', 'ticket'], name='idx_quality_company_ticket'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.ticket_id}:{self.get_status_display()} x{self.defect_qty}'


class QualityInspection(BaseModel):
    """路线节点检验单；确认后才通过 manufacturing 服务登记数量事件。"""

    class InspectionType(models.TextChoices):
        SEMI = 'semi_inspection', '半成品检验'
        FINAL = 'final_inspection', '成品尾查'
        REINSPECTION = 'reinspection', '返修后二次复检'

    class Status(models.TextChoices):
        PENDING = 'pending', '待确认'
        CONFIRMED = 'confirmed', '已确认'

    company = models.ForeignKey(
        'accounts.Company', models.CASCADE, related_name='node_inspections', verbose_name='公司')
    ticket = models.ForeignKey(
        'catalog.Ticket', models.PROTECT, related_name='node_inspections', verbose_name='菲票')
    route = models.ForeignKey(
        'manufacturing.ProductionRoute', models.PROTECT,
        related_name='quality_inspections', verbose_name='生产路线')
    node = models.ForeignKey(
        'manufacturing.RouteNode', models.PROTECT,
        related_name='quality_inspections', verbose_name='检验节点')
    inspection_type = models.CharField('检验类型', max_length=32, choices=InspectionType.choices)
    status = models.CharField(
        '状态', max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    inspected = models.PositiveIntegerField('检验数量')
    qualified = models.PositiveIntegerField('合格数量', default=0)
    rework = models.PositiveIntegerField('返修数量', default=0)
    scrapped = models.PositiveIntegerField('报废数量', default=0)
    rework_node = models.ForeignKey(
        'manufacturing.RouteNode', models.PROTECT, null=True, blank=True,
        related_name='quality_rework_inspections', verbose_name='返修目标节点')
    reinspection_of = models.ForeignKey(
        'self', models.PROTECT, null=True, blank=True,
        related_name='reinspections', verbose_name='原检验单')
    reason = models.CharField('原因/备注', max_length=255, blank=True, default='')
    inspector = models.ForeignKey(
        'accounts.User', models.SET_NULL, null=True,
        related_name='node_quality_inspections', verbose_name='检验人')
    inspected_at = models.DateTimeField('检验时间', default=timezone.now)
    confirmed_by = models.ForeignKey(
        'accounts.User', models.SET_NULL, null=True, blank=True,
        related_name='confirmed_node_quality_inspections', verbose_name='确认人')
    confirmed_at = models.DateTimeField('确认时间', null=True, blank=True)
    request_id = models.CharField('确认幂等请求号', max_length=60, null=True, blank=True)
    create_request_id = models.CharField('建单幂等请求号', max_length=64, blank=True, default='')
    create_fingerprint = models.CharField('建单请求指纹', max_length=64, blank=True, default='')

    class Meta:
        db_table = 'quality_inspection'
        verbose_name = '节点检验单'
        verbose_name_plural = verbose_name
        ordering = ['-inspected_at', '-id']
        constraints = [
            models.CheckConstraint(condition=Q(inspected__gt=0),
                                   name='ck_quality_inspection_positive'),
            models.CheckConstraint(
                condition=Q(inspected=F('qualified') + F('rework') + F('scrapped')),
                name='ck_quality_inspection_quantity_sum'),
            models.CheckConstraint(
                condition=(Q(rework=0, rework_node__isnull=True)
                           | Q(rework__gt=0, rework_node__isnull=False)),
                name='ck_quality_inspection_rework_target'),
            models.UniqueConstraint(
                fields=['company', 'request_id'],
                condition=Q(is_deleted=False, request_id__isnull=False),
                name='uq_quality_inspection_request'),
            models.UniqueConstraint(
                fields=['company', 'create_request_id'], condition=~Q(create_request_id=''),
                name='uq_quality_inspection_create_request'),
        ]
        indexes = [
            models.Index(fields=['company', 'status', 'inspected_at'],
                         name='idx_quality_insp_co_status'),
            models.Index(fields=['company', 'ticket', 'node'],
                         name='idx_quality_insp_ticket_node'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.ticket_id}:{self.node_id}:{self.inspection_type}'
