"""出货域模型（P2 出货管理，docs/服装ERP全模块设计方案.md §4.2）。

Shipment：出货单（一单挂一个客户订单，明细按色码行拆）。
  - 未交量真口径（P1 §3.3 近似口径自此废弃）：OrderItem.qty − Σ已出量
    （已出 = 已审核出货单明细同色码合计，作废单不计）；
  - status 状态机：draft →(审核)→ confirmed(该订单未交量清零) / partial(部分出货)；
    shipped/signed 为发运/签收预留态（动作端点随 P3 质检/物流落地，当前不可达）；
  - 部分出货不自动拆单（调研确认）：剩余量在未交矩阵（GET /{id}/undelivered）
    里显示为「未交」，后续出货另开新单。

ShipmentItem：出货明细（色码×尺码×数量）。审核时三道校验（services.audit_shipment）：
  ①未交量  Σ已出+本次 ≤ OrderItem.qty（400 硬拦）；
  ②成品负库存 400 硬拦——口径差异必须写明：面料超领是生产弹性（可退可补、
    落 over 标记零拦截），成品出货超完工量 = 仓库发不出货的物理错误，
    静默过账会制造假出库，必须拦截；
  ③缺价 400——OrderItem.price 为 null 的色码行提示补价（衔接 P1「缺价显示—
    并提示补价」），审核通过即按 Σqty×price 自动生成应收（finance.create_receivable）。

PackingBox：装箱单行（箱号/毛净重/箱规），审核出货单与打印装箱单消费。

ShipmentPrintLog：打印记录，复用 TicketPrintLog 模式（print_key 幂等
  print_key=shipment:{id}:{tpl}，条件唯一约束防重计数；is_reprint 补打标记）。

所有唯一约束带 is_deleted 条件（项目教训：软删行占用业务键）；
作废走软删（is_deleted=True）+ ops audit 留痕，已生成的应收不自动撤
（撤单不自动撤应收——资金凭证须人工对账处理，void 响应 warning 提示）。
"""
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class Shipment(BaseModel):
    """出货单：UNIQUE(company, shipment_no, is_deleted=False)。"""

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        CONFIRMED = 'confirmed', '已出货（全部）'
        PARTIAL = 'partial', '部分出货'
        SHIPPED = 'shipped', '已发运'
        SIGNED = 'signed', '已签收'

    company = models.ForeignKey('accounts.Company', verbose_name='公司',
                                on_delete=models.CASCADE,
                                related_name='shipments')
    shipment_no = models.CharField('出货单号', max_length=32, db_index=True,
                                   help_text='留空自动生成 SH+日期+3 位顺延')
    order = models.ForeignKey('orders.Order', verbose_name='客户订单',
                              on_delete=models.CASCADE,
                              related_name='shipments',
                              help_text='一单挂一个订单；未交量/应收均以订单为锚')
    customer = models.ForeignKey('catalog.Customer', verbose_name='客户',
                                 on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='shipments',
                                 help_text='必传（出货单必挂客户，应收联动依赖）')
    delivery_date = models.DateField('交货日期', null=True, blank=True)
    ship_date = models.DateField('发货日期', null=True, blank=True)
    status = models.CharField('状态', max_length=16, choices=Status.choices,
                              default=Status.DRAFT,
                              help_text='draft→审核→confirmed(订单未交量清零)/'
                                        'partial(部分出货)；shipped/signed 预留')
    boxes = models.PositiveIntegerField('箱数', default=0,
                                        help_text='装箱行数自动汇总，可手工覆盖为 0 表示未装箱')
    total_qty = models.PositiveIntegerField('出货总数量', default=0,
                                            help_text='Σ明细 qty，保存/审核时自动汇总')
    logistics_no = models.CharField('物流单号', max_length=64, blank=True, default='')
    audited_by = models.ForeignKey('accounts.User', verbose_name='审核人',
                                   on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='audited_shipments')
    audited_at = models.DateTimeField('审核时间', null=True, blank=True)
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'shipment'
        verbose_name = '出货单'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'shipment_no'],
                                    condition=Q(is_deleted=False),
                                    name='uq_shipment_company_no_active'),
        ]
        indexes = [
            models.Index(fields=['company', 'status'], name='idx_ship_company_status'),
            models.Index(fields=['company', 'order'], name='idx_ship_company_order'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.shipment_no}'


class ShipmentItem(BaseModel):
    """出货明细：色码 × 尺码 × 数量（同单同色码唯一，软删防撞）。

    审核校验的量纲锚点：qty 须 ≤ 该订单该色码未交量（真口径）且 ≤
    完工结存量（负库存拦截），见 apps.shipment.services.audit_shipment。
    """

    shipment = models.ForeignKey(Shipment, verbose_name='出货单',
                                 on_delete=models.CASCADE,
                                 related_name='items')
    color = models.CharField('颜色', max_length=64)
    size = models.CharField('尺码', max_length=32)
    qty = models.PositiveIntegerField('数量',
                                      help_text='>0；审核时按未交量/负库存/缺价三道校验')

    class Meta:
        db_table = 'shipment_item'
        verbose_name = '出货明细'
        verbose_name_plural = verbose_name
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['shipment', 'color', 'size'],
                                    condition=Q(is_deleted=False),
                                    name='uq_shipment_item_ship_color_size'),
        ]

    def __str__(self):
        return f'{self.shipment_id}:{self.color}/{self.size}x{self.qty}'


class PackingBox(BaseModel):
    """装箱单行：箱号 + 色码 + 数量 + 毛净重 + 箱规（打印装箱单数据源）。"""

    shipment = models.ForeignKey(Shipment, verbose_name='出货单',
                                 on_delete=models.CASCADE,
                                 related_name='packing_boxes')
    box_no = models.CharField('箱号', max_length=32)
    color = models.CharField('颜色', max_length=64, blank=True, default='')
    size = models.CharField('尺码', max_length=32, blank=True, default='')
    qty = models.PositiveIntegerField('数量', default=0)
    gross_wt = models.DecimalField('毛重(kg)', max_digits=10, decimal_places=2,
                                   null=True, blank=True)
    net_wt = models.DecimalField('净重(kg)', max_digits=10, decimal_places=2,
                                 null=True, blank=True)
    box_spec = models.CharField('箱规', max_length=64, blank=True, default='')

    class Meta:
        db_table = 'packing_box'
        verbose_name = '装箱单行'
        verbose_name_plural = verbose_name
        ordering = ['box_no', 'id']

    def __str__(self):
        return f'{self.shipment_id}:{self.box_no}'


class ShipmentPrintLog(BaseModel):
    """出货单/装箱单打印记录（TicketPrintLog 模式复刻）。

    print_key = shipment:{id}:{tpl}（tpl=shipment/packing）由前端生成上报；
    同一 print_key 只允许一条（条件唯一约束，print_key 为空的历史上报不参与）；
    is_reprint = 上报时点该单是否已有本模板打印记录（首打 False、补打 True）。
    """

    class Template(models.TextChoices):
        SHIPMENT = 'shipment', '出货单'
        PACKING = 'packing', '装箱单'

    company = models.ForeignKey('accounts.Company', verbose_name='公司',
                                on_delete=models.CASCADE,
                                related_name='shipment_print_logs')
    shipment = models.ForeignKey(Shipment, verbose_name='出货单',
                                 on_delete=models.CASCADE,
                                 related_name='print_logs')
    template = models.CharField('打印模板', max_length=16,
                                choices=Template.choices,
                                default=Template.SHIPMENT)
    operator = models.ForeignKey('accounts.User', verbose_name='操作人',
                                 on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='shipment_print_logs')
    print_key = models.CharField('打印幂等键', max_length=64, null=True, blank=True,
                                 db_index=True)
    is_reprint = models.BooleanField('补打', default=False)

    class Meta:
        db_table = 'shipment_print_log'
        verbose_name = '出货打印记录'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['print_key', 'shipment'],
                                    condition=Q(print_key__isnull=False),
                                    name='uq_shipment_printlog_key_shipment'),
        ]

    def __str__(self):
        return f'{self.shipment_id} {self.template} key={self.print_key}'
