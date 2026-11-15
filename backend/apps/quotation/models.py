"""报价管理模型（P2 报价域，docs/服装ERP全模块设计方案.md §4.8）。

决策依据（docs/P1设计决策表.md D2/D3/B3 + 现场确认清单-业界对照表.md）：
  - D2：加工费=工价总和×倍率，倍率可配区间 1.8-3.0 默认 2.3（超界给 warning
    字段提示、不硬禁——小厂没单 1.8 也做、精品厂乘 3 是真实惯例）；
    小单附加费（上机费/制版费）为单独报价金额项 addon_amount。
  - B3：报价单耗与大货单耗两级精度（业界三级 YPD，本项目落两级）——
    QuoteMaterial.quote_unit_usage 为报价估料，与 tech.BomItem.unit_usage
    （大货定额）互不干扰；选了 bom_item 行只是带出参照，报价侧可改。
  - D3：完工/出货回填对比=软提醒不拦截——backfill-compare 端点纯只读，
    任何差异都不阻塞业务动作；完成/出货后的提醒展示属前端职责。
全表继承 BaseModel（软删 + created_at/updated_at），惯例照抄 apps/cost。
"""
from decimal import Decimal

from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import BaseModel


class Quotation(BaseModel):
    """报价单：UNIQUE(company, quote_no, is_deleted=False)。

    quote_no 自动号规则：Q+日期(YYYYMMDD)+'-'+3 位流水（同公司同日顺延，
    软删段不复用流水——all_objects 扫描防撞）。
    total_price/profit_rate 均为算定字段（保存时由 services 重算，不接受手填）：
      total_price  = Σ 材料(quote_unit_usage×price×qty) + labor.labor_total
      profit_rate  = (total_price − cost_assumed) / total_price × 100，
                     cost_assumed = 材料 + labor_base（工价实际人工）+ addon_amount
                     （附加费按过手成本处理，保守口径）；total=0 时为 null。
    order：won 后可关联现有订单——回填对比（backfill-compare）靠它对接 OrderCost。
    """

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        SENT = 'sent', '已发送'
        WON = 'won', '已成交'
        LOST = 'lost', '已流失'

    company = models.ForeignKey('accounts.Company', verbose_name='公司',
                                on_delete=models.CASCADE,
                                related_name='quotations')
    quote_no = models.CharField('报价单号', max_length=32, db_index=True,
                                help_text='自动号：Q+YYYYMMDD-流水')
    customer = models.ForeignKey('catalog.Customer', verbose_name='客户',
                                 on_delete=models.SET_NULL,
                                 related_name='quotations',
                                 null=True, blank=True)
    style = models.ForeignKey('catalog.Style', verbose_name='款式',
                              on_delete=models.PROTECT,
                              related_name='quotations')
    qty = models.PositiveIntegerField('报价数量')
    quote_date = models.DateField('报价日期', default=timezone.localdate)
    material_total = models.DecimalField('材料合计', max_digits=12, decimal_places=2,
                                         default=Decimal('0'),
                                         help_text='算定：Σ quote_unit_usage×price×qty')
    total_price = models.DecimalField('报价总额', max_digits=12, decimal_places=2,
                                      default=Decimal('0'),
                                      help_text='算定：材料合计 + 人工合计')
    profit_rate = models.DecimalField('利润率%', max_digits=6, decimal_places=2,
                                      null=True, blank=True,
                                      help_text='算定：(total−cost_assumed)/total×100，'
                                                'total=0 时为 null')
    status = models.CharField('状态', max_length=8, choices=Status.choices,
                              default=Status.DRAFT, db_index=True)
    order = models.ForeignKey('orders.Order', verbose_name='关联订单',
                              on_delete=models.SET_NULL, related_name='quotations',
                              null=True, blank=True,
                              help_text='won 后可关联；回填对比的取数锚点')
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'quotation'
        verbose_name = '报价单'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'quote_no'],
                                    condition=Q(is_deleted=False),
                                    name='uq_quotation_company_quote_no_active'),
        ]
        indexes = [
            models.Index(fields=['company', 'status'], name='idx_quote_co_status'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.quote_no}'


class QuoteMaterial(BaseModel):
    """报价用料行：报价估料（两级精度，与大货定额 BomItem.unit_usage 分离）。

    bom_item 可空——选了定额行仅作带出参照（material/usage 预填），报价侧可改；
    amount 为计算属性不落库（=quote_unit_usage×price，单件用料额）。
    """

    quotation = models.ForeignKey(Quotation, verbose_name='报价单',
                                  on_delete=models.CASCADE,
                                  related_name='materials')
    material = models.ForeignKey('tech.Material', verbose_name='物料',
                                 on_delete=models.PROTECT,
                                 related_name='quote_materials')
    bom_item = models.ForeignKey('tech.BomItem', verbose_name='BOM定额行',
                                 on_delete=models.SET_NULL,
                                 related_name='quote_materials',
                                 null=True, blank=True,
                                 help_text='可空：选定额行带出参照，报价估料可改')
    quote_unit_usage = models.DecimalField('报价单耗', max_digits=12, decimal_places=4,
                                           help_text='报价估料（B3 两级精度），'
                                                     '与大货单耗 BomItem.unit_usage 分离')
    price = models.DecimalField('单价', max_digits=12, decimal_places=3)

    class Meta:
        db_table = 'quotation_material'
        verbose_name = '报价用料'
        verbose_name_plural = verbose_name
        ordering = ['id']

    @property
    def amount(self) -> Decimal:
        """单件用料额 = 报价单耗 × 单价（计算属性，不落库）。"""
        return self.quote_unit_usage * self.price

    def __str__(self):
        return f'{self.quotation_id}:{self.material_id} x{self.quote_unit_usage}'


class QuoteLabor(BaseModel):
    """报价人工（每报价单一行）：工价总和×倍率 + 小单溢价 + 附加费。

    rate  超界（<1.8 或 >3.0）只置 rate_warning=True 提示、不硬禁（D2 拍板：
          区间可配但现实中 1.8-3.0 之外都发生，拦截不成立）。
    small_lot_addon  小单溢价百分比：qty<300 时默认 +30%，可改；
          addon_rule 存规则 JSON {'threshold','addon_pct','auto_applied'}。
    addon_amount  小单附加费金额（上机费/制版费等，单独报价行项，D2）。
    labor_total 算定 = labor_base×rate×(1+small_lot_addon/100) + addon_amount。
    """

    quotation = models.OneToOneField(Quotation, verbose_name='报价单',
                                     on_delete=models.CASCADE,
                                     related_name='labor')
    labor_base = models.DecimalField('工序工价总和', max_digits=12, decimal_places=3,
                                     default=Decimal('0'),
                                     help_text='手录或由款式工序工价快照合计带出')
    rate = models.DecimalField('倍率', max_digits=5, decimal_places=2,
                               default=Decimal('2.3'),
                               help_text='默认 2.3；超 1.8-3.0 区间置 rate_warning 不拦截')
    rate_warning = models.BooleanField('倍率超界提示', default=False,
                                       help_text='D2：超界只提示不硬禁')
    small_lot_addon = models.DecimalField('小单溢价%', max_digits=5, decimal_places=2,
                                          default=Decimal('0'),
                                          help_text='qty<300 时默认 30（+30%），可改')
    addon_rule = models.JSONField('小单溢价规则', default=dict, blank=True,
                                  help_text="{'threshold':300,'addon_pct':30,"
                                            "'auto_applied':bool}")
    addon_amount = models.DecimalField('附加费', max_digits=12, decimal_places=2,
                                       default=Decimal('0'),
                                       help_text='上机费/制版费等单独报价金额项')
    labor_total = models.DecimalField('人工合计', max_digits=12, decimal_places=2,
                                      default=Decimal('0'),
                                      help_text='算定：base×rate×(1+addon%)+addon_amount')

    class Meta:
        db_table = 'quotation_labor'
        verbose_name = '报价人工'
        verbose_name_plural = verbose_name
        ordering = ['id']

    def __str__(self):
        return f'{self.quotation_id}: base={self.labor_base} x{self.rate}'
