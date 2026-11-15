"""薪酬结算数据模型。

Settlement：结算单（把一段期间内未结算登记锁定为金额快照，
避免之后改价影响已发工资）；
SalaryAdjust：管理员对员工当期工资的人工调整（正负金额）；
SettleApply：员工发起的结算申请，状态机 待审核→待发放→已发放。
"""
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class Settlement(BaseModel):
    """结算单：期间内登记记录的金额快照。"""

    class Status(models.TextChoices):
        SETTLED = 'settled', '已结算'
        REVOKED = 'revoked', '已撤销'

    company = models.ForeignKey(
        'accounts.Company', models.CASCADE, related_name='settlements',
        verbose_name='公司')
    user = models.ForeignKey(
        'accounts.User', models.CASCADE, null=True, blank=True,
        related_name='settlements', verbose_name='结算员工',
        help_text='为空表示全员结算')
    period_start = models.DateField('结算起始')
    period_end = models.DateField('结算截止')
    employee_count = models.PositiveIntegerField('员工数', default=0)
    amount = models.DecimalField(
        '结算金额', max_digits=12, decimal_places=2, default=0,
        help_text='快照金额，四舍五入到分，避免之后改价影响')
    wage_detail = models.JSONField(
        '分员工金额快照', null=True, blank=True,
        help_text='{user_id: amount}：四类工资制后结算金额按人快照，'
                  '供员工卡 settled 口径精确归属（全员结算单必填）')
    status = models.CharField(
        '状态', max_length=10, choices=Status.choices, default=Status.SETTLED)
    created_by = models.ForeignKey(
        'accounts.User', models.SET_NULL, null=True, blank=True,
        related_name='created_settlements', verbose_name='创建人')

    class Meta:
        db_table = 'settlement'
        verbose_name = '结算单'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        indexes = [
            models.Index(fields=['company', 'status'],
                         name='idx_settlement_comp_status'),
        ]

    def __str__(self):
        return (f'{self.company_id}:{self.period_start}'
                f'~{self.period_end}:{self.amount}')


class SalaryAdjust(BaseModel):
    """工资调整：管理员手工对员工当期工资加减。"""

    company = models.ForeignKey(
        'accounts.Company', models.CASCADE, related_name='salary_adjusts',
        verbose_name='公司')
    user = models.ForeignKey(
        'accounts.User', models.CASCADE, related_name='salary_adjusts',
        verbose_name='调整员工')
    amount = models.DecimalField(
        '调整金额', max_digits=10, decimal_places=2, default=0,
        help_text='正数增加、负数扣减')
    reason = models.CharField('事由', max_length=200)
    operator = models.ForeignKey(
        'accounts.User', models.SET_NULL, null=True, blank=True,
        related_name='operated_salary_adjusts', verbose_name='操作人')

    class Meta:
        db_table = 'salary_adjust'
        verbose_name = '工资调整'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        indexes = [
            models.Index(fields=['company', 'user', 'created_at'],
                         name='idx_salary_adjust_comp_user'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.user_id}:{self.amount}'


class SettleApply(BaseModel):
    """结算申请：员工申请结算本人某期间未结登记，管理员审核后发放。"""

    class Status(models.IntegerChoices):
        PENDING = 0, '待审核'
        APPROVED = 1, '待发放'
        PAID = 2, '已发放'
        REJECTED = 3, '已拒绝'
        VOIDED = 4, '已作废'

    company = models.ForeignKey(
        'accounts.Company', models.CASCADE, related_name='settle_applies',
        verbose_name='公司')
    applicant = models.ForeignKey(
        'accounts.User', models.CASCADE, related_name='settle_applies',
        verbose_name='申请人')
    user = models.ForeignKey(
        'accounts.User', models.CASCADE, related_name='applied_settles',
        verbose_name='结算员工',
        help_text='当前为本人申请，与 applicant 相同')
    period_start = models.DateField('结算起始')
    period_end = models.DateField('结算截止')
    amount = models.DecimalField(
        '申请金额', max_digits=12, decimal_places=2, default=0,
        help_text='发起时未结登记金额快照')
    status = models.SmallIntegerField(
        '状态', choices=Status.choices, default=Status.PENDING)
    note = models.CharField('备注', max_length=200, blank=True, default='')
    reviewer = models.ForeignKey(
        'accounts.User', models.SET_NULL, null=True, blank=True,
        related_name='reviewed_settle_applies', verbose_name='审核人')
    reviewed_at = models.DateTimeField('审核时间', null=True, blank=True)
    reject_reason = models.CharField('拒绝理由', max_length=200,
                                     blank=True, default='')
    paid_by = models.ForeignKey(
        'accounts.User', models.SET_NULL, null=True, blank=True,
        related_name='paid_settle_applies', verbose_name='发放人')
    paid_at = models.DateTimeField('发放时间', null=True, blank=True)
    payer = models.CharField('收款人', max_length=64, blank=True, default='',
                             help_text='发放时的收款人记录')
    void_reason = models.CharField('作废理由', max_length=200,
                                   blank=True, default='')

    class Meta:
        db_table = 'settle_apply'
        verbose_name = '结算申请'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        indexes = [
            models.Index(fields=['company', 'user', 'status'],
                         name='idx_settle_apply_comp_user'),
        ]

    def __str__(self):
        return (f'{self.company_id}:{self.user_id}:'
                f'{self.period_start}~{self.period_end}')


class SalaryPayment(BaseModel):
    """实付工资事实；作废只停止抵扣，不删除支付及分摊记录。"""

    class Status(models.TextChoices):
        ACTIVE = 'active', '有效'
        VOID = 'void', '已作废'

    company = models.ForeignKey(
        'accounts.Company', models.CASCADE, related_name='salary_payments',
        verbose_name='公司')
    employee = models.ForeignKey(
        'accounts.User', models.PROTECT, related_name='salary_payments',
        verbose_name='员工')
    paid_at = models.DateTimeField('实付时间')
    method = models.CharField('支付方式', max_length=32)
    transaction_ref = models.CharField('外部流水号', max_length=100)
    note = models.CharField('备注', max_length=500, blank=True, default='')
    operator = models.ForeignKey(
        'accounts.User', models.SET_NULL, null=True, blank=True,
        related_name='operated_salary_payments', verbose_name='操作人')
    amount = models.DecimalField(
        '实付金额', max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))])
    status = models.CharField(
        '状态', max_length=10, choices=Status.choices, default=Status.ACTIVE)
    void_reason = models.CharField(
        '作废原因', max_length=500, blank=True, default='')
    voided_by = models.ForeignKey(
        'accounts.User', models.SET_NULL, null=True, blank=True,
        related_name='voided_salary_payments', verbose_name='作废人')
    voided_at = models.DateTimeField('作废时间', null=True, blank=True)

    class Meta:
        db_table = 'salary_payment'
        verbose_name = '实付工资'
        verbose_name_plural = verbose_name
        ordering = ['-paid_at', '-id']
        indexes = [
            models.Index(fields=['company', 'employee', 'paid_at'],
                         name='idx_salarypay_co_emp_time'),
            models.Index(fields=['company', 'status'],
                         name='idx_salarypay_co_status'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'transaction_ref'],
                condition=Q(is_deleted=False, status='active'),
                name='uniq_salarypay_active_ref'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.employee_id}:{self.amount}'


class SalaryPaymentAllocation(BaseModel):
    """一笔实付对一张有效结算单的员工归属金额。"""

    payment = models.ForeignKey(
        SalaryPayment, models.CASCADE, related_name='allocations',
        verbose_name='实付工资')
    settlement = models.ForeignKey(
        Settlement, models.PROTECT, related_name='payment_allocations',
        verbose_name='结算单')
    amount = models.DecimalField(
        '分摊金额', max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))])

    class Meta:
        db_table = 'salary_payment_allocation'
        verbose_name = '实付工资分摊'
        verbose_name_plural = verbose_name
        ordering = ['id']
        indexes = [
            models.Index(fields=['settlement', 'payment'],
                         name='idx_salaryalloc_settle_pay'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['payment', 'settlement'],
                name='uniq_salaryalloc_pay_settle'),
        ]
