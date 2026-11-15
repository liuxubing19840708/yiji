"""应收域模型（P2 finance 域，docs/服装ERP全模块设计方案.md §4.3）。

Receivable：应收账行。出货域（SA-P2-4）自动建单与手工建单共用
create_receivable 服务入口（services.py，含单号自动/公司校验/审计）。
  - amount 与 received_amount 并行推进：received_amount 只由核销
    （ReceiptLine）在行级 select_for_update 事务内推进；
  - status 由核销/撤销重算：received>=amount → settled，>0 → partial，=0 → unpaid；
  - revenue（成本域理论收入）与应收实收差异属预期（折扣/尾数/坏账）——
    本 app 即 §3.3「P2 应收核销后以收款实绩复核」的落点。

ReceiptLine：收款核销流水。超额收款 400 硬拦（金额级）。口径差异说明
（与领料超领零拦截对照，见 services.add_receipt docstring）：领料多领
是生产弹性可回冲；多收的钱是要退的资金错误，必须拦截。

shipment 出货单关联：P2 出货域（SA-P2-4）接入后为真 FK（shipment.Shipment，
SET_NULL）；0001 曾以整列占位，finance 迁移 0002 RemoveField+AddField 平移
（占位列只可能存悬空整数——出货表彼时尚不存在，无可回填真行，零丢失）。

所有唯一约束带 is_deleted 条件（项目教训：软删行占用业务键）；
作废走软删（is_deleted=True）+ ops audit 留痕，状态机只保留三态。
"""
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class Receivable(BaseModel):
    """应收账行：UNIQUE(company, receivable_no, is_deleted=False)。"""

    class Status(models.TextChoices):
        UNPAID = 'unpaid', '未收'
        PARTIAL = 'partial', '部分收款'
        SETTLED = 'settled', '已结清'

    company = models.ForeignKey('accounts.Company', verbose_name='公司',
                                on_delete=models.CASCADE,
                                related_name='finance_receivables')
    receivable_no = models.CharField('应收单号', max_length=32, db_index=True,
                                     help_text='留空自动生成 AR+日期+3 位顺延；'
                                               '手工端不传，出货域自动建亦走服务生成')
    customer = models.ForeignKey('catalog.Customer', verbose_name='客户',
                                 on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='finance_receivables',
                                 help_text='删客户不影响应收（SET_NULL，同订单域惯例）')
    order = models.ForeignKey('orders.Order', verbose_name='客户订单',
                              on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='finance_receivables',
                              help_text='可空：手工应收可不挂单')
    # P2 出货域（SA-P2-4，2026-10-24 接入）：真 FK（迁移 0002 由整列占位平移，
    # RemoveField+AddField 顺序见 finance/migrations/0002）；出货审核自动建应收
    # 时回填，SET_NULL（作废出货单不影响应收行）
    shipment = models.ForeignKey('shipment.Shipment', verbose_name='出货单',
                                 on_delete=models.SET_NULL, null=True, blank=True,
                                 db_index=True, related_name='finance_receivables',
                                 help_text='出货审核自动生成应收时回填（SA-P2-4）')
    amount = models.DecimalField('应收金额', max_digits=12, decimal_places=2)
    received_amount = models.DecimalField('已收金额', max_digits=12, decimal_places=2,
                                          default=0,
                                          help_text='只由核销/撤销事务内推进，禁直改')
    status = models.CharField('状态', max_length=10, choices=Status.choices,
                              default=Status.UNPAID)
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'finance_receivable'
        verbose_name = '应收账'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'receivable_no'],
                                    condition=Q(is_deleted=False),
                                    name='uq_finance_company_ar_no_active'),
        ]
        indexes = [
            models.Index(fields=['company', 'status'], name='idx_fin_ar_company_status'),
            models.Index(fields=['company', 'customer'], name='idx_fin_ar_company_cust'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.receivable_no}'


class ReceiptLine(BaseModel):
    """收款核销流水：每行一次收款动作，撤销=软删最近一行并反向回退。"""

    class Method(models.TextChoices):
        CASH = 'cash', '现款'
        MONTHLY = 'monthly', '月结'
        ACCEPTANCE = 'acceptance', '承兑'
        OTHER = 'other', '其他'

    company = models.ForeignKey('accounts.Company', verbose_name='公司',
                                on_delete=models.CASCADE,
                                related_name='finance_receipt_lines',
                                help_text='冗余公司列：流水直查免 join 应收（IssueItem 无'
                                          '公司列的教训，隔离查询一律带上）')
    receivable = models.ForeignKey(Receivable, verbose_name='应收账',
                                   on_delete=models.CASCADE,
                                   related_name='receipt_lines')
    amount = models.DecimalField('核销金额', max_digits=12, decimal_places=2,
                                 help_text='>0；超额收款 400 硬拦（services.add_receipt）')
    method = models.CharField('收款方式', max_length=16, choices=Method.choices,
                              default=Method.CASH)
    received_at = models.DateTimeField('收款时间')
    handler = models.CharField('经手人', max_length=32, blank=True, default='',
                               help_text='手填经手人姓名（收款人未必是系统用户）')
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'finance_receipt_line'
        verbose_name = '收款核销'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        indexes = [
            models.Index(fields=['receivable', 'is_deleted'], name='idx_fin_rl_rec_live'),
            models.Index(fields=['company', 'received_at'], name='idx_fin_rl_company_time'),
        ]

    def __str__(self):
        return f'{self.receivable_id}:{self.amount}'


class Payable(BaseModel):
    """采购应付；金额由财务确认，不从缺换算参数的采购行猜测。"""

    class Status(models.TextChoices):
        UNPAID = 'unpaid', '未付'
        PARTIAL = 'partial', '部分付款'
        SETTLED = 'settled', '已付清'

    company = models.ForeignKey('accounts.Company', models.CASCADE,
                                related_name='finance_payables', verbose_name='公司')
    payable_no = models.CharField('应付单号', max_length=32)
    supplier = models.ForeignKey('warehouse.Supplier', models.SET_NULL,
                                 null=True, blank=True,
                                 related_name='finance_payables', verbose_name='供应商')
    supplier_name = models.CharField('供应商名称快照', max_length=128)
    purchase = models.ForeignKey('warehouse.PurchaseOrder', models.SET_NULL,
                                 null=True, blank=True,
                                 related_name='finance_payables', verbose_name='采购单')
    amount = models.DecimalField('应付金额', max_digits=12, decimal_places=2)
    paid_amount = models.DecimalField('已付金额', max_digits=12, decimal_places=2,
                                      default=0)
    status = models.CharField('状态', max_length=10, choices=Status.choices,
                              default=Status.UNPAID)
    payable_at = models.DateField('应付日期')
    due_at = models.DateField('到期日期', null=True, blank=True)
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'finance_payable'
        ordering = ['-id']
        constraints = [models.UniqueConstraint(
            fields=['company', 'payable_no'], condition=Q(is_deleted=False),
            name='uq_fin_ap_company_no_active')]
        indexes = [
            models.Index(fields=['company', 'status'], name='idx_fin_ap_comp_status'),
            models.Index(fields=['company', 'payable_at'], name='idx_fin_ap_comp_date'),
            models.Index(fields=['company', 'due_at'], name='idx_fin_ap_comp_due'),
        ]


class SupplierPayment(BaseModel):
    """真实供应商付款流水；作废保留事实并停止抵扣应付。"""

    class Status(models.TextChoices):
        ACTIVE = 'active', '有效'
        VOIDED = 'voided', '已作废'

    class Method(models.TextChoices):
        CASH = 'cash', '现金'
        BANK = 'bank', '银行转账'
        ACCEPTANCE = 'acceptance', '承兑'
        OTHER = 'other', '其他'

    company = models.ForeignKey('accounts.Company', models.CASCADE,
                                related_name='supplier_payments', verbose_name='公司')
    payable = models.ForeignKey(Payable, models.PROTECT, null=True, blank=True,
                                related_name='payments', verbose_name='核销应付')
    supplier = models.ForeignKey('warehouse.Supplier', models.SET_NULL,
                                 null=True, blank=True,
                                 related_name='payments', verbose_name='供应商')
    supplier_name = models.CharField('供应商名称快照', max_length=128)
    amount = models.DecimalField('付款金额', max_digits=12, decimal_places=2)
    paid_at = models.DateTimeField('付款时间')
    method = models.CharField('付款方式', max_length=16, choices=Method.choices,
                              default=Method.BANK)
    transaction_ref = models.CharField('外部流水号', max_length=64,
                                       blank=True, default='')
    status = models.CharField('状态', max_length=10, choices=Status.choices,
                              default=Status.ACTIVE)
    operator = models.ForeignKey('accounts.User', models.SET_NULL,
                                 null=True, blank=True, related_name='+',
                                 verbose_name='操作人')
    voided_by = models.ForeignKey('accounts.User', models.SET_NULL,
                                  null=True, blank=True, related_name='+',
                                  verbose_name='作废人')
    voided_at = models.DateTimeField('作废时间', null=True, blank=True)
    void_reason = models.CharField('作废原因', max_length=200,
                                   blank=True, default='')
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'finance_supplier_payment'
        ordering = ['-id']
        constraints = [models.UniqueConstraint(
            fields=['company', 'transaction_ref'],
            condition=Q(is_deleted=False, status='active') & ~Q(transaction_ref=''),
            name='uq_fin_sp_company_ref_active')]
        indexes = [
            models.Index(fields=['company', 'paid_at'], name='idx_fin_sp_comp_paid'),
            models.Index(fields=['payable', 'status'], name='idx_fin_sp_payable_status'),
        ]


class CashEntry(BaseModel):
    """手工登记的其他实际收支；工资、回款、供应商付款由各自事实表统计。"""

    class Direction(models.TextChoices):
        EXPENSE = 'expense', '费用支出'
        INCOME = 'income', '其他收入'

    class Status(models.TextChoices):
        ACTIVE = 'active', '有效'
        VOIDED = 'voided', '已作废'

    company = models.ForeignKey('accounts.Company', models.CASCADE,
                                related_name='cash_entries', verbose_name='公司')
    entry_no = models.CharField('收支单号', max_length=32)
    direction = models.CharField('方向', max_length=10, choices=Direction.choices)
    category = models.CharField('类别', max_length=64)
    amount = models.DecimalField('金额', max_digits=12, decimal_places=2)
    occurred_at = models.DateTimeField('业务时间')
    counterparty = models.CharField('往来方', max_length=128,
                                    blank=True, default='')
    method = models.CharField('收支方式', max_length=32, blank=True, default='')
    reference_no = models.CharField('外部凭证号', max_length=64,
                                    blank=True, default='')
    status = models.CharField('状态', max_length=10, choices=Status.choices,
                              default=Status.ACTIVE)
    operator = models.ForeignKey('accounts.User', models.SET_NULL,
                                 null=True, blank=True, related_name='+',
                                 verbose_name='操作人')
    voided_by = models.ForeignKey('accounts.User', models.SET_NULL,
                                  null=True, blank=True, related_name='+',
                                  verbose_name='作废人')
    voided_at = models.DateTimeField('作废时间', null=True, blank=True)
    void_reason = models.CharField('作废原因', max_length=200,
                                   blank=True, default='')
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'finance_cash_entry'
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'entry_no'],
                                    condition=Q(is_deleted=False),
                                    name='uq_fin_ce_company_no_active'),
            models.UniqueConstraint(fields=['company', 'reference_no'],
                                    condition=Q(is_deleted=False, status='active')
                                    & ~Q(reference_no=''),
                                    name='uq_fin_ce_company_ref_active'),
        ]
        indexes = [
            models.Index(fields=['company', 'occurred_at'], name='idx_fin_ce_comp_time'),
            models.Index(fields=['company', 'direction', 'category'],
                         name='idx_fin_ce_comp_kind'),
        ]
