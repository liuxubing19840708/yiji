"""轻量仓库 + 质量追溯数据模型（设计文档 docs/quality-traceability-design.md §3）。

用户拍板（2026-10-18）：
- 追溯锚点 = 面料批次（Lot），批次号沿用供应商/染厂原始编号手填，不自动编码；
- 血缘 = 床次级（一扎不跨批，菲票随床次共享血缘），血缘表记「床次×批次×用量」；
- 来源单号 = Production.source_order_no 手填字段，不建客户订单模型；
- 仓库只记流水不记结存；领料单人确认即生效（confirm 钩子自动写血缘）。
所有唯一约束带 is_deleted 条件（项目教训：软删行占用业务键）。
"""
from django.db import models

from apps.core.models import BaseModel


class Lot(BaseModel):
    """面料批次：追溯的最小原材料锚点。

    P0 阶段管理员/生产岗手工登记（建床/编菲页填写，床次详情可补填改）；
    P1 起由入库行自动 get_or_create（批次号 = 供应商原始编号）。
    """

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='lots', verbose_name='公司')
    lot_no = models.CharField('批次号', max_length=64,
                              help_text='供应商/染厂原始编号，手填不重造')
    fabric_name = models.CharField('面料名称', max_length=64, blank=True, default='')
    color = models.CharField('颜色', max_length=64, blank=True, default='')
    qty = models.DecimalField('数量', max_digits=12, decimal_places=2, default=0)
    unit = models.CharField('单位', max_length=16, blank=True, default='米')
    batch_date = models.DateField('批次日期', null=True, blank=True)
    source_in = models.ForeignKey('warehouse.StockInItem', on_delete=models.SET_NULL,
                                  null=True, blank=True, related_name='lots',
                                  verbose_name='来源入库行', help_text='P1 入库时回填')
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'lot'
        verbose_name = '面料批次'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'lot_no'], condition=models.Q(is_deleted=False),
                                    name='uq_lot_company_no_active'),
        ]
        indexes = [
            models.Index(fields=['company', 'lot_no'], name='idx_lot_company_no'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.lot_no}'


class ProductionLotLink(BaseModel):
    """血缘表：床次 × 批次 × 用量（一床多批次 = 多行；菲票随床次共享血缘）。"""

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='production_lot_links', verbose_name='公司')
    production = models.ForeignKey('production.Production', on_delete=models.CASCADE,
                                   related_name='lot_links', verbose_name='床次')
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT,
                            related_name='production_links', verbose_name='面料批次')
    qty = models.DecimalField('用布数量', max_digits=12, decimal_places=2, default=0,
                              help_text='该床次耗用此批次的数量，0=未细分')

    class Meta:
        db_table = 'production_lot_link'
        verbose_name = '床次批次血缘'
        verbose_name_plural = verbose_name
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['production', 'lot'], condition=models.Q(is_deleted=False),
                                    name='uq_prod_lot_link_pair_active'),
        ]
        indexes = [
            models.Index(fields=['company', 'lot'], name='idx_pll_company_lot'),
            models.Index(fields=['production'], name='idx_pll_production'),
        ]

    def __str__(self):
        return f'{self.production_id}:{self.lot_id}:{self.qty}'


class PurchaseOrder(BaseModel):
    """采购单（轻量流水：谁、何时、买了什么布；不做财务勾稽）。"""

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        DONE = 'done', '完成'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='purchase_orders', verbose_name='公司')
    order_no = models.CharField('采购单号', max_length=32)
    supplier_name = models.CharField('供应商', max_length=128, blank=True, default='')
    ordered_at = models.DateField('采购日期')
    status = models.CharField('状态', max_length=16, choices=Status.choices,
                              default=Status.DRAFT)
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'purchase_order'
        verbose_name = '采购单'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'order_no'], condition=models.Q(is_deleted=False),
                                    name='uq_po_company_no_active'),
        ]
        indexes = [
            models.Index(fields=['company', 'status'], name='idx_po_company_status'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.order_no}'


class PurchaseItem(BaseModel):
    """采购单行：面料名称/颜色/数量/单价（单价仅记录）。"""

    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE,
                              related_name='items', verbose_name='采购单')
    fabric_name = models.CharField('面料名称', max_length=64)
    color = models.CharField('颜色', max_length=64, blank=True, default='')
    qty = models.DecimalField('数量', max_digits=12, decimal_places=2, default=0)
    unit = models.CharField('单位', max_length=16, blank=True, default='米')
    price = models.DecimalField('单价', max_digits=10, decimal_places=3, default=0)

    class Meta:
        db_table = 'purchase_item'
        verbose_name = '采购单行'
        verbose_name_plural = verbose_name
        ordering = ['id']

    def __str__(self):
        return f'{self.order_id}:{self.fabric_name}'


class StockIn(BaseModel):
    """入库单：布进厂流水，行级产生/回填面料批次（追溯数据起点）。"""

    class SourceType(models.TextChoices):
        PURCHASE = 'purchase', '采购入库'
        RETURN = 'return', '退货入库'
        OTHER = 'other', '其他'

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        DONE = 'done', '完成'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='stock_ins', verbose_name='公司')
    in_no = models.CharField('入库单号', max_length=32)
    source_type = models.CharField('来源', max_length=16, choices=SourceType.choices,
                                   default=SourceType.PURCHASE)
    purchase = models.ForeignKey(PurchaseOrder, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='stock_ins', verbose_name='关联采购单')
    in_at = models.DateTimeField('入库时间')
    status = models.CharField('状态', max_length=16, choices=Status.choices,
                              default=Status.DRAFT)
    operator = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='stock_ins', verbose_name='经办人')
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'stock_in'
        verbose_name = '入库单'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'in_no'], condition=models.Q(is_deleted=False),
                                    name='uq_stockin_company_no_active'),
        ]
        indexes = [
            models.Index(fields=['company', 'status'], name='idx_stockin_company_status'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.in_no}'


class StockInItem(BaseModel):
    """入库行：批次在此诞生——保存时按 (company, lot_no) get_or_create Lot 并回填来源。"""

    stock_in = models.ForeignKey(StockIn, on_delete=models.CASCADE,
                                 related_name='items', verbose_name='入库单')
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, null=True, blank=True,
                            related_name='stock_in_items', verbose_name='面料批次')
    fabric_name = models.CharField('面料名称', max_length=64)
    color = models.CharField('颜色', max_length=64, blank=True, default='')
    lot_no = models.CharField('批次号', max_length=64)
    qty = models.DecimalField('数量', max_digits=12, decimal_places=2, default=0)
    unit = models.CharField('单位', max_length=16, blank=True, default='米')

    class Meta:
        db_table = 'stock_in_item'
        verbose_name = '入库单行'
        verbose_name_plural = verbose_name
        ordering = ['id']

    def __str__(self):
        return f'{self.stock_in_id}:{self.lot_no}'


class MaterialIssue(BaseModel):
    """领料单：车间领布到床次——确认（confirmed）时自动写床次×批次血缘。"""

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        CONFIRMED = 'confirmed', '已确认'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='material_issues', verbose_name='公司')
    issue_no = models.CharField('领料单号', max_length=32)
    issue_at = models.DateTimeField('领料时间')
    status = models.CharField('状态', max_length=16, choices=Status.choices,
                              default=Status.DRAFT)
    operator = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='material_issues', verbose_name='经办人')
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'material_issue'
        verbose_name = '领料单'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'issue_no'], condition=models.Q(is_deleted=False),
                                    name='uq_issue_company_no_active'),
        ]
        indexes = [
            models.Index(fields=['company', 'status'], name='idx_issue_company_status'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.issue_no}'


class IssueItem(BaseModel):
    """领料单行：床次 + 批次 + 数量。"""

    issue = models.ForeignKey(MaterialIssue, on_delete=models.CASCADE,
                              related_name='items', verbose_name='领料单')
    production = models.ForeignKey('production.Production', on_delete=models.PROTECT,
                                   related_name='issue_items', verbose_name='领用床次')
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT,
                            related_name='issue_items', verbose_name='面料批次')
    qty = models.DecimalField('数量', max_digits=12, decimal_places=2, default=0)

    class Meta:
        db_table = 'material_issue_item'
        verbose_name = '领料单行'
        verbose_name_plural = verbose_name
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['issue', 'production', 'lot'],
                                    condition=models.Q(is_deleted=False),
                                    name='uq_issue_item_active'),
        ]

    def __str__(self):
        return f'{self.issue_id}:{self.production_id}:{self.lot_id}'
