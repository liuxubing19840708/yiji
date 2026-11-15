"""订单成本模型（P1 cost 域，docs/服装ERP全模块设计方案.md §3.3）。

OrderCost：重算式汇总表（每订单一行，非流水）。取数链三路核对定稿 2026-10-24：
  material_cost  = Σ 已确认领料行 主单位qty × lot.unit_cost（退料冲减；
                   缺价/0价行不进成本、计 missing_price_cnt）
  labor_cost     = process 类 Σ Record.qty × Record.price（全量含已结算，
                   禁引 StyleProcess 现价）
                   + whole 类按完成菲票票面均摊额合计（piece_rate÷参与人数，
                   不读 Record.price，与 wage.py:66-99 同源）
  outsourcing_cost = 0（P3 委外接入后自动纳入）
  overhead_cost  = 手工录入（PUT /cost/orders/{id}/overhead，MVP 不做自动分摊）
  revenue        = Σ OrderItem.qty × OrderItem.price（理论收入，
                   与实际收款差异属预期——折扣/尾数/坏账，P2 应收复核）
commission 差额不进任何订单（工资对账单列示）；fixed 为期间费用不入订单成本。
"""
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class OrderCost(BaseModel):
    """订单成本汇总行：UNIQUE(company, order, is_deleted=False)。"""

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        COMPUTED = 'computed', '已计算'

    company = models.ForeignKey('accounts.Company', verbose_name='公司',
                                on_delete=models.CASCADE,
                                related_name='order_costs')
    order = models.ForeignKey('orders.Order', verbose_name='客户订单',
                              on_delete=models.CASCADE,
                              related_name='cost_record')
    material_cost = models.DecimalField('材料成本', max_digits=12, decimal_places=2,
                                        default=0)
    labor_cost = models.DecimalField('人工成本', max_digits=12, decimal_places=2,
                                     default=0,
                                     help_text='process=Σqty×price（全量含已结算）'
                                               '+ whole=完成菲票票面均摊额合计')
    outsourcing_cost = models.DecimalField('委外成本', max_digits=12, decimal_places=2,
                                           default=0,
                                           help_text='P1 恒 0，P3 委外接入后自动纳入')
    overhead_cost = models.DecimalField('制费/管理费', max_digits=12, decimal_places=2,
                                        default=0,
                                        help_text='手工录入（MVP 不做自动分摊）')
    total_cost = models.DecimalField('总成本', max_digits=12, decimal_places=2, default=0)
    output_qty = models.PositiveIntegerField('产出件数', default=0,
                                             help_text='P2 前口径=全部登记完成扎数×扎量（近似）')
    unit_cost = models.DecimalField('单件成本', max_digits=12, decimal_places=3,
                                    null=True, blank=True,
                                    help_text='=total_cost/output_qty，产出 0 时为 null')
    revenue = models.DecimalField('理论收入', max_digits=12, decimal_places=2, default=0,
                                  help_text='Σ OrderItem.qty×price；与实际收款差异属预期')
    no_order_cnt = models.PositiveIntegerField('未归属床次数', default=0,
                                               help_text='公司级口径：有成本活动但未挂单的床次数'
                                                         '（其成本不计入任何订单，防毛利虚高静默漏算）')
    missing_price_cnt = models.PositiveIntegerField('缺价行数', default=0,
                                                    help_text='已确认领料中 lot 无价/0价/'
                                                              '换算缺参的行数（不进材料成本）')
    gross_profit = models.DecimalField('毛利', max_digits=12, decimal_places=2, default=0)
    gross_margin = models.DecimalField('毛利率%', max_digits=6, decimal_places=2,
                                       null=True, blank=True,
                                       help_text='=毛利/理论收入×100，收入 0 时为 null')
    status = models.CharField('状态', max_length=10, choices=Status.choices,
                              default=Status.DRAFT)
    computed_at = models.DateTimeField('计算时间', null=True, blank=True)
    computed_by = models.ForeignKey('accounts.User', verbose_name='计算人',
                                    on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='computed_order_costs')
    # 同步 stale 埋点（无 signals/celery，已核）：MaterialIssue 确认 / records 登记·撤销 /
    # OrderItem.price 修改 / overhead PUT —— 事务内 filter(is_stale=False).update(is_stale=True)
    is_stale = models.BooleanField('数据已过期', default=False,
                                   help_text='上游单据变动后为 True，待重算')

    class Meta:
        db_table = 'cost_order_cost'
        verbose_name = '订单成本'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'order'],
                                    condition=Q(is_deleted=False),
                                    name='uq_cost_company_order_active'),
        ]
        indexes = [
            models.Index(fields=['company', 'status'], name='idx_cost_company_status'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.order_id}:{self.total_cost}'
