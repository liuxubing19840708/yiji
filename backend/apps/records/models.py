"""扫菲登记数据模型。

Record：员工在某张菲票的某道工序上完成的件数登记。
同一菲票同一工序同一员工仅一条记录（防重复登记）；
协助登记时 operator 记录实际操作人；
settlement 非空表示已被薪酬结算锁定（快照）。
"""
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import BaseModel


class Record(BaseModel):
    """扫菲登记记录。"""

    company = models.ForeignKey(
        'accounts.Company', models.CASCADE, related_name='records', verbose_name='公司')
    ticket = models.ForeignKey(
        'catalog.Ticket', models.CASCADE, related_name='records', verbose_name='菲票')
    process = models.ForeignKey(
        'catalog.Process', models.CASCADE, related_name='records', verbose_name='工序')
    user = models.ForeignKey(
        'accounts.User', models.CASCADE, related_name='records',
        verbose_name='被登记员工', help_text='件数归属的员工')
    operator = models.ForeignKey(
        'accounts.User', models.SET_NULL, null=True, blank=True,
        related_name='operated_records', verbose_name='操作人',
        help_text='协助登记时记录实际操作人，与 user 不同')
    qty = models.PositiveIntegerField('件数', default=0)
    # 次品数预留列（2026-10-20 DDL 批准）：一期只建列不写逻辑，二期次品/返工闭环消费
    qty_defect = models.PositiveIntegerField('次品数预留', default=0)
    price = models.DecimalField('登记单价', max_digits=10, decimal_places=3, default=0)
    registered_at = models.DateTimeField('登记时间', default=timezone.now)
    settlement = models.ForeignKey(
        'salary.Settlement', models.SET_NULL, null=True, blank=True,
        related_name='records', verbose_name='所属结算单',
        help_text='非空表示已进入结算快照，禁止再改动/撤销')

    class Meta:
        db_table = 'record'
        verbose_name = '扫菲登记'
        verbose_name_plural = verbose_name
        ordering = ['-registered_at', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'ticket', 'process', 'user'], condition=Q(is_deleted=False),
                name='uq_record_ticket_process_user'),
        ]
        indexes = [
            models.Index(fields=['company', 'user', 'registered_at'],
                         name='idx_record_company_user_date'),
            models.Index(fields=['company', 'ticket'], name='idx_record_company_ticket'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.ticket_id}:{self.process_id}:{self.user_id}'


class ScanIdempotency(models.Model):
    """扫菲 POST 的公司级幂等凭据与首次响应快照。

    Record 会按同票/同工序/同员工聚合并更新，因此不能只靠 Record 唯一约束：
    重试必须返回首次业务结果，而不是再次累加或返回已被后续补登改变的记录。
    """

    company = models.ForeignKey(
        'accounts.Company', models.CASCADE, related_name='scan_idempotency_keys',
        verbose_name='公司')
    key = models.CharField('幂等键', max_length=128)
    payload_hash = models.CharField('规范化载荷 SHA-256', max_length=64)
    operator = models.ForeignKey(
        'accounts.User', models.CASCADE, related_name='scan_idempotency_keys',
        verbose_name='首次操作人')
    record = models.ForeignKey(
        Record, models.SET_NULL, null=True, blank=True,
        related_name='idempotency_receipts', verbose_name='首次登记记录')
    response_data = models.JSONField('首次响应快照', default=dict, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'scan_idempotency'
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'key'], name='uq_scan_idempotency_company_key'),
        ]
        indexes = [
            models.Index(fields=['company', 'created_at'],
                         name='idx_scan_idem_company_created'),
        ]
