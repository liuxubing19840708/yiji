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
    # P1 库存价链（D4）：批次单价（与缸号绑定），入库确认时由 StockInItem.unit_price
    # 带出（首个非空价），也允许仓库岗手工补录（挂 warehouse.manage，存量手工批次靠此收敛）
    unit_cost = models.DecimalField('批次单价', max_digits=10, decimal_places=3,
                                    null=True, blank=True,
                                    help_text='入库价折算或补录；缺价（NULL/0）不进材料成本')
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


class Supplier(BaseModel):
    """供应商主档（P2 采购补全域 §4.1）：公司内 code 唯一（软删防撞）。

    P2 权限点 warehouse.supplier（legacy 默认零授权，供应商档案独立管控）；
    被未删除的采购单/暂收单引用时接口层拒绝删除（PROTECT 语义，软删留痕）。
    settle_method 结算方式（现结/月结/预付…自由文本）；rating 评级字段预留。
    """

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='suppliers', verbose_name='公司')
    code = models.CharField('供应商编码', max_length=64)
    name = models.CharField('供应商名称', max_length=128)
    contact_person = models.CharField('联系人', max_length=64, blank=True, default='')
    phone = models.CharField('电话', max_length=32, blank=True, default='')
    settle_method = models.CharField('结算方式', max_length=32, blank=True, default='',
                                     help_text='现结/月结/预付等，自由文本')
    rating = models.CharField('评级', max_length=16, blank=True, default='',
                              help_text='评级字段预留（A/B/C…）')
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'supplier'
        verbose_name = '供应商'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'code'], condition=models.Q(is_deleted=False),
                                    name='uq_supplier_company_code_active'),
        ]
        indexes = [
            models.Index(fields=['company', 'name'], name='idx_supplier_company_name'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.code} {self.name}'


class PurchaseOrder(BaseModel):
    """采购单（轻量流水：谁、何时、买了什么布；不做财务勾稽）。

    P2 加 supplier FK（可空，存量零破坏）：挂主档后需求汇总/在途统计可挂供应商；
    保留 supplier_name 自由文本（无主档采购照常用）。
    """

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        DONE = 'done', '完成'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='purchase_orders', verbose_name='公司')
    order_no = models.CharField('采购单号', max_length=32)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name='purchase_orders', verbose_name='供应商')
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
    """采购单行：面料名称/颜色/数量/单价（单价仅记录）。

    P2 加列（可空/有默认，存量零破坏）：
      demand_source  需求来源 manual(手工)/summary_import(需求汇总导入)
      arrived_qty    已到料量（暂收确认时累计，在途=qty−arrived_qty）
      qualified_qty  已合格量（IQC 后累计，不驱动库存，留痕口径）
      material       物料关联（可空）——需求汇总/在途按物料聚合的锚点；
                     存量行 NULL=不入物料维统计（口径按行过滤）
    """

    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE,
                              related_name='items', verbose_name='采购单')
    fabric_name = models.CharField('面料名称', max_length=64)
    color = models.CharField('颜色', max_length=64, blank=True, default='')
    qty = models.DecimalField('数量', max_digits=12, decimal_places=2, default=0)
    unit = models.CharField('单位', max_length=16, blank=True, default='米')
    price = models.DecimalField('单价', max_digits=10, decimal_places=3, default=0,
                                help_text='恒为主单位规范化值（D19）；原报单位见 price_unit')
    # D19 单价单位（2026-10-24）：记录用户原报单价单位；price 存储一律主单位规范化
    price_unit = models.CharField('单价单位', max_length=2, blank=True, default='',
                                  choices=[('', '按主单位'), ('kg', '元/公斤'), ('m', '元/米')],
                                  null=True,
                                  help_text='用户原报单价单位（空=按主单位）；存储价已规范化')
    demand_source = models.CharField('需求来源', max_length=16,
                                     choices=[('manual', '手工'), ('summary_import', '汇总导入')],
                                     default='manual')
    arrived_qty = models.DecimalField('已到料量', max_digits=12, decimal_places=2, default=0)
    qualified_qty = models.DecimalField('已合格量', max_digits=12, decimal_places=2, default=0)
    material = models.ForeignKey('tech.Material', on_delete=models.PROTECT,
                                 null=True, blank=True, related_name='purchase_items',
                                 verbose_name='物料')

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
        OPENING = 'opening', '期初入库'  # D1 期初库存 Excel 导入（上线落地方案 §11-D1）

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
    # P1 库存价链（D4）：行级单价 + 可空行级采购行 FK（P0 仅单头级 FK，已核）。
    # 入库创建/确认时 unit_price 缺省从 purchase_item.price 带出，无关联则手录。
    # D19：unit_price 恒为主单位规范化值；price_unit 记录用户原报单价单位
    unit_price = models.DecimalField('入库单价', max_digits=10, decimal_places=3,
                                     null=True, blank=True)
    price_unit = models.CharField('单价单位', max_length=2, blank=True, default='',
                                  choices=[('', '按主单位'), ('kg', '元/公斤'), ('m', '元/米')],
                                  null=True,
                                  help_text='用户原报单价单位（空=按主单位）；存储价已规范化')
    purchase_item = models.ForeignKey(PurchaseItem, on_delete=models.PROTECT,
                                      null=True, blank=True,
                                      related_name='stock_in_items',
                                      verbose_name='关联采购单行')
    # P2 需求汇总库存口径的物料锚点（可空，存量零破坏）：暂收确认生成的入库行
    # 自动带出暂收行的物料；手工入库可选填，NULL=不入物料维库存统计
    material = models.ForeignKey('tech.Material', on_delete=models.PROTECT,
                                 null=True, blank=True, related_name='stock_in_items',
                                 verbose_name='物料')

    class Meta:
        db_table = 'stock_in_item'
        verbose_name = '入库单行'
        verbose_name_plural = verbose_name
        ordering = ['id']

    def __str__(self):
        return f'{self.stock_in_id}:{self.lot_no}'


class MaterialIssue(BaseModel):
    """领料单：车间领布到床次——确认（confirmed）时自动写床次×批次血缘。

    P1 增 type（退料载体，材料成本只增不减的防线）：return 单行 qty 为正数=退回仓库量，
    确认时冲减血缘已领量；退料同样挂床次+Lot 留痕。
    """

    class Type(models.TextChoices):
        ISSUE = 'issue', '领料'
        RETURN = 'return', '退料'

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        CONFIRMED = 'confirmed', '已确认'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='material_issues', verbose_name='公司')
    issue_no = models.CharField('领料单号', max_length=32)
    type = models.CharField('单据类型', max_length=8, choices=Type.choices,
                            default=Type.ISSUE)
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
    """领料单行：床次 + 批次 + 数量。

    P1 定额化（全部可空列，存量行 NULL=不启用定额，零破坏）：
      bom_item  定额来源行（选定额行自动带出 material/unit/qty_req）
      material  物料关联（无 BOM 可手选——进「无定额领料占比」统计）
      qty_req   定额数量（单位=Material 主单位）= 单耗×(1+损耗率)×床次件数
                （床次件数基数 Production.plan_qty 回落 total_qty，服务端算定）
      over      超额标记（登记侧零拦截：qty 折算主单位后 > qty_req 即落 True）
      over_reason 超额原因（可选，管理侧差异报表追责用）
    """

    issue = models.ForeignKey(MaterialIssue, on_delete=models.CASCADE,
                              related_name='items', verbose_name='领料单')
    production = models.ForeignKey('production.Production', on_delete=models.PROTECT,
                                   related_name='issue_items', verbose_name='领用床次')
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT,
                            related_name='issue_items', verbose_name='面料批次')
    qty = models.DecimalField('数量', max_digits=12, decimal_places=2, default=0)
    bom_item = models.ForeignKey('tech.BomItem', on_delete=models.PROTECT,
                                 null=True, blank=True, related_name='issue_items',
                                 verbose_name='定额来源行')
    material = models.ForeignKey('tech.Material', on_delete=models.PROTECT,
                                 null=True, blank=True, related_name='issue_items',
                                 verbose_name='物料')
    qty_req = models.DecimalField('定额数量', max_digits=12, decimal_places=2,
                                  null=True, blank=True,
                                  help_text='单位=Material 主单位；NULL=无定额')
    over = models.BooleanField('超额标记', default=False)
    over_reason = models.CharField('超额原因', max_length=255, blank=True, default='')

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


# ============ P2 到料暂收（§4.1）：暂收 → IQC 简版拆量 → 确认生成采购入库 ============

class Receipt(BaseModel):
    """到料暂收单：PO 行（或无单直收）到货先暂存，IQC 判定后确认入库。

    流转：draft（暂收，行可改可重判）→ confirm（确认动作）：
      - 行级 IQC：qualified_qty/rejected_qty 拆量（合格/拒收）；
      - 合格量自动生成 StockIn(type=purchase)，StockInItem 带 purchase_item +
        unit_price=PurchaseItem.price（复用 P1 价链，价格自动带出）；
      - 拒收量留痕不进库存（不写 StockIn/Lot）；
      - PurchaseItem.arrived_qty/qualified_qty 累计（在途=qty−arrived_qty 随之收敛）。
    confirmed 后不可改；删除仅限 draft。无单直收 purchase=NULL（source_type 仍为
    purchase，unit_price 无价链来源为 NULL）。
    """

    class Status(models.TextChoices):
        DRAFT = 'draft', '暂收'
        CONFIRMED = 'confirmed', '已确认'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='receipts', verbose_name='公司')
    receipt_no = models.CharField('暂收单号', max_length=32)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name='receipts', verbose_name='供应商')
    supplier_name = models.CharField('供应商名称', max_length=128, blank=True, default='',
                                     help_text='无主档直收时手填')
    purchase = models.ForeignKey(PurchaseOrder, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='receipts', verbose_name='关联采购单')
    status = models.CharField('状态', max_length=16, choices=Status.choices,
                              default=Status.DRAFT)
    confirmed_at = models.DateTimeField('确认时间', null=True, blank=True)
    confirmed_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='receipts',
                                     verbose_name='确认人')
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'receipt'
        verbose_name = '到料暂收单'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'receipt_no'],
                                    condition=models.Q(is_deleted=False),
                                    name='uq_receipt_company_no_active'),
        ]
        indexes = [
            models.Index(fields=['company', 'status'], name='idx_receipt_company_status'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.receipt_no}'


class ReceiptItem(BaseModel):
    """暂收单行：PO 行（可空=无单直收）+ 物料/批次信息 + 到料拆量。

    arrived_qty 暂收量；qualified_qty/rejected_qty 为 IQC 行级判定拆量
    （合格+拒收与到料的差额=未判定量，简版不做拦截，确认时仅合格量入库存）。
    material 可空：需求汇总库存口径与入库行的物料锚点（NULL=不入物料维统计）。
    """

    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE,
                                related_name='items', verbose_name='暂收单')
    purchase_item = models.ForeignKey(PurchaseItem, on_delete=models.PROTECT,
                                      null=True, blank=True, related_name='receipt_items',
                                      verbose_name='关联采购单行')
    material = models.ForeignKey('tech.Material', on_delete=models.PROTECT,
                                 null=True, blank=True, related_name='receipt_items',
                                 verbose_name='物料')
    fabric_name = models.CharField('面料名称', max_length=64, blank=True, default='')
    color = models.CharField('颜色', max_length=64, blank=True, default='')
    lot_no = models.CharField('批次号/缸号', max_length=64)
    unit = models.CharField('单位', max_length=16, blank=True, default='米')
    arrived_qty = models.DecimalField('到料数量', max_digits=12, decimal_places=2, default=0)
    qualified_qty = models.DecimalField('合格数量', max_digits=12, decimal_places=2, default=0)
    rejected_qty = models.DecimalField('拒收数量', max_digits=12, decimal_places=2, default=0)
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'receipt_item'
        verbose_name = '暂收单行'
        verbose_name_plural = verbose_name
        ordering = ['id']

    def __str__(self):
        return f'{self.receipt_id}:{self.lot_no}:{self.arrived_qty}'
