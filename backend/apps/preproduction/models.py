"""产前域数据模型。

所有业务写入由 services.py 完成。审批保存工艺、样衣、问题、交底及修订号的
SHA-256 事实快照；get_active_approval 每次重新校验，历史审批不能掩盖后续业务变化。
"""
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class TechPackVersion(BaseModel):
    """订单款式的工艺包版本；关键内容变更会改变 content_hash。"""

    company = models.ForeignKey('accounts.Company', models.CASCADE,
                                related_name='tech_pack_versions', verbose_name='公司')
    order = models.ForeignKey('orders.Order', models.CASCADE,
                              related_name='tech_pack_versions', verbose_name='订单')
    style = models.ForeignKey('catalog.Style', models.PROTECT,
                              related_name='tech_pack_versions', verbose_name='款式')
    version_no = models.PositiveIntegerField('版本号')
    pattern_no = models.CharField('纸样编号', max_length=64, blank=True, default='')
    pattern_files = models.JSONField('纸样附件路径', default=list, blank=True)
    measurements = models.JSONField('结构化尺寸表', default=list, blank=True)
    tolerances = models.JSONField('尺寸公差', default=dict, blank=True)
    attachments = models.JSONField('工艺附件', default=list, blank=True)
    requirements = models.TextField('工艺要求', blank=True, default='')
    challenges = models.TextField('工艺难点', blank=True, default='')
    common_mistakes = models.TextField('易错点', blank=True, default='')
    quality_standards = models.TextField('品质标准', blank=True, default='')
    content_hash = models.CharField('关键工艺内容指纹', max_length=64, editable=False)
    revision = models.PositiveIntegerField('产前事实修订号', default=1, editable=False)
    is_current = models.BooleanField('订单当前版本', default=True, db_index=True)
    created_by = models.ForeignKey('accounts.User', models.SET_NULL, null=True,
                                   related_name='created_tech_packs', verbose_name='创建人')

    class Meta:
        db_table = 'preproduction_tech_pack'
        ordering = ['order_id', '-version_no', '-id']
        constraints = [
            models.UniqueConstraint(fields=['order', 'version_no'],
                                    condition=Q(is_deleted=False),
                                    name='uq_pp_pack_order_version'),
            models.UniqueConstraint(fields=['order'], condition=Q(is_deleted=False, is_current=True),
                                    name='uq_pp_pack_order_current'),
        ]
        indexes = [models.Index(fields=['company', 'order', 'is_current'],
                                name='idx_pp_pack_co_order_current')]

    def __str__(self):
        return f'{self.order_id}:V{self.version_no}'


class SampleTask(BaseModel):
    class Status(models.TextChoices):
        PENDING = 'pending', '待打样'
        PASSED = 'passed', '合格'
        FAILED = 'failed', '不合格'
        REWORK = 'rework', '重做中'

    company = models.ForeignKey('accounts.Company', models.CASCADE,
                                related_name='preproduction_samples', verbose_name='公司')
    tech_pack = models.ForeignKey(TechPackVersion, models.CASCADE,
                                  related_name='sample_tasks', verbose_name='工艺版本')
    title = models.CharField('产前样任务', max_length=100)
    assignee = models.ForeignKey('accounts.User', models.SET_NULL, null=True, blank=True,
                                 related_name='preproduction_sample_tasks', verbose_name='负责人')
    status = models.CharField('状态', max_length=16, choices=Status.choices,
                              default=Status.PENDING, db_index=True)
    created_by = models.ForeignKey('accounts.User', models.SET_NULL, null=True,
                                   related_name='+', verbose_name='创建人')

    class Meta:
        db_table = 'preproduction_sample_task'
        ordering = ['id']
        indexes = [models.Index(fields=['company', 'tech_pack', 'status'],
                                name='idx_pp_sample_pack_status')]


class SampleRecord(BaseModel):
    """产前样每次检验/重做均新增事实，不覆盖历史。"""

    class Result(models.TextChoices):
        PASSED = 'passed', '合格'
        FAILED = 'failed', '不合格'
        REWORK = 'rework', '重做'

    company = models.ForeignKey('accounts.Company', models.CASCADE, related_name='+', verbose_name='公司')
    task = models.ForeignKey(SampleTask, models.CASCADE, related_name='records', verbose_name='产前样任务')
    result = models.CharField('结果', max_length=16, choices=Result.choices)
    note = models.TextField('检验/重做说明', blank=True, default='')
    attachments = models.JSONField('样品附件路径', default=list, blank=True)
    operator = models.ForeignKey('accounts.User', models.SET_NULL, null=True,
                                 related_name='+', verbose_name='操作人')
    occurred_at = models.DateTimeField('记录时间', auto_now_add=True)

    class Meta:
        db_table = 'preproduction_sample_record'
        ordering = ['id']
        indexes = [models.Index(fields=['company', 'task', 'occurred_at'],
                                name='idx_pp_srecord_task_time')]


class ReviewIssue(BaseModel):
    class Status(models.TextChoices):
        OPEN = 'open', '待整改'
        RECTIFIED = 'rectified', '待复审'
        CLOSED = 'closed', '复审关闭'

    company = models.ForeignKey('accounts.Company', models.CASCADE,
                                related_name='preproduction_issues', verbose_name='公司')
    tech_pack = models.ForeignKey(TechPackVersion, models.CASCADE,
                                  related_name='review_issues', verbose_name='工艺版本')
    title = models.CharField('问题', max_length=200)
    detail = models.TextField('问题说明', blank=True, default='')
    status = models.CharField('状态', max_length=16, choices=Status.choices,
                              default=Status.OPEN, db_index=True)
    rectification = models.TextField('整改说明', blank=True, default='')
    review_note = models.TextField('复审意见', blank=True, default='')
    raised_by = models.ForeignKey('accounts.User', models.SET_NULL, null=True,
                                  related_name='+', verbose_name='提出人')
    reviewed_by = models.ForeignKey('accounts.User', models.SET_NULL, null=True, blank=True,
                                    related_name='+', verbose_name='复审人')
    reviewed_at = models.DateTimeField('复审时间', null=True, blank=True)

    class Meta:
        db_table = 'preproduction_review_issue'
        ordering = ['id']
        indexes = [models.Index(fields=['company', 'tech_pack', 'status'],
                                name='idx_pp_issue_pack_status')]


class BriefingTarget(BaseModel):
    company = models.ForeignKey('accounts.Company', models.CASCADE,
                                related_name='preproduction_briefings', verbose_name='公司')
    tech_pack = models.ForeignKey(TechPackVersion, models.CASCADE,
                                  related_name='briefing_targets', verbose_name='工艺版本')
    employee = models.ForeignKey('accounts.User', models.CASCADE,
                                 related_name='preproduction_briefings', verbose_name='交底员工')
    note = models.CharField('交底说明', max_length=255, blank=True, default='')
    created_by = models.ForeignKey('accounts.User', models.SET_NULL, null=True,
                                   related_name='+', verbose_name='创建人')

    class Meta:
        db_table = 'preproduction_briefing_target'
        ordering = ['id']
        constraints = [models.UniqueConstraint(fields=['tech_pack', 'employee'],
                                               condition=Q(is_deleted=False),
                                               name='uq_pp_brief_pack_employee')]


class BriefingReceipt(BaseModel):
    """员工本人签收事实；签收后不可代签或重复签收。"""

    company = models.ForeignKey('accounts.Company', models.CASCADE, related_name='+', verbose_name='公司')
    target = models.OneToOneField(BriefingTarget, models.CASCADE,
                                  related_name='receipt', verbose_name='交底对象')
    employee = models.ForeignKey('accounts.User', models.CASCADE,
                                 related_name='preproduction_receipts', verbose_name='签收员工')
    signed_at = models.DateTimeField('签收时间', auto_now_add=True)
    remark = models.CharField('签收备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'preproduction_briefing_receipt'
        ordering = ['id']
        constraints = [models.UniqueConstraint(fields=['employee', 'target'],
                                               name='uq_pp_receipt_employee_target')]


class BriefingConfirmation(BaseModel):
    """生产负责人对当前工艺内容的一次交底确认，历史记录不覆盖。"""

    company = models.ForeignKey('accounts.Company', models.CASCADE,
                                related_name='preproduction_briefing_confirmations', verbose_name='公司')
    tech_pack = models.ForeignKey(TechPackVersion, models.CASCADE,
                                  related_name='briefing_confirmations', verbose_name='工艺版本')
    content_hash = models.CharField('确认时工艺指纹', max_length=64)
    note = models.TextField('交底说明', blank=True, default='')
    confirmed_by = models.ForeignKey('accounts.User', models.SET_NULL, null=True,
                                     related_name='+', verbose_name='确认人')
    confirmed_at = models.DateTimeField('确认时间', auto_now_add=True)

    class Meta:
        db_table = 'preproduction_briefing_confirmation'
        ordering = ['-confirmed_at', '-id']
        constraints = [models.UniqueConstraint(
            fields=['tech_pack', 'content_hash'], condition=Q(is_deleted=False),
            name='uq_pp_brief_confirm_pack_hash')]
        indexes = [models.Index(fields=['company', 'tech_pack', 'content_hash'],
                                name='idx_pp_bconfirm_pack_hash')]


class CraftTemplate(BaseModel):
    """公司工艺模板；company 为空为只读系统文本模板。"""

    class Kind(models.TextChoices):
        MEASUREMENTS = 'measurements', '尺寸表'
        REQUIREMENTS = 'requirements', '工艺要求'
        CHALLENGES = 'challenges', '工艺难点'
        COMMON_MISTAKES = 'common_mistakes', '易错点'
        QUALITY_STANDARDS = 'quality_standards', '品质标准'

    company = models.ForeignKey('accounts.Company', models.CASCADE, null=True, blank=True,
                                related_name='preproduction_craft_templates', verbose_name='公司')
    kind = models.CharField('模板类型', max_length=24, choices=Kind.choices, db_index=True)
    name = models.CharField('模板名称', max_length=80)
    content = models.JSONField('模板内容')
    created_by = models.ForeignKey('accounts.User', models.SET_NULL, null=True,
                                   related_name='+', verbose_name='创建人')

    class Meta:
        db_table = 'preproduction_craft_template'
        ordering = ['kind', 'id']
        constraints = [models.UniqueConstraint(
            fields=['company', 'kind', 'name'], condition=Q(is_deleted=False),
            name='uq_pp_template_company_kind_name')]
        indexes = [models.Index(fields=['company', 'kind'], name='idx_pp_template_co_kind')]

    @property
    def is_system(self):
        return self.company_id is None


class PreproductionApproval(BaseModel):
    class Status(models.TextChoices):
        APPROVED = 'approved', '通过'
        REJECTED = 'rejected', '驳回'
        INVALIDATED = 'invalidated', '已失效（重新审批）'

    company = models.ForeignKey('accounts.Company', models.CASCADE,
                                related_name='preproduction_approvals', verbose_name='公司')
    order = models.ForeignKey('orders.Order', models.CASCADE,
                              related_name='preproduction_approvals', verbose_name='订单')
    tech_pack = models.ForeignKey(TechPackVersion, models.CASCADE,
                                  related_name='approvals', verbose_name='工艺版本')
    content_hash = models.CharField('审批时工艺指纹', max_length=64)
    status = models.CharField('审批结果', max_length=16, choices=Status.choices)
    reason = models.TextField('审批意见', blank=True, default='')
    decided_by = models.ForeignKey('accounts.User', models.SET_NULL, null=True,
                                   related_name='+', verbose_name='审批人')
    decided_at = models.DateTimeField('审批时间', auto_now_add=True)

    class Meta:
        db_table = 'preproduction_approval'
        ordering = ['-decided_at', '-id']
        constraints = [models.UniqueConstraint(fields=['tech_pack'],
                                               condition=Q(is_deleted=False, status='approved'),
                                               name='uq_pp_approval_pack_approved')]
        indexes = [models.Index(fields=['company', 'order', 'status'],
                                name='idx_pp_appr_co_order_status')]


class PreproductionAuditEvent(BaseModel):
    """产前域不可变审计事实（业务层不提供修改/删除接口）。"""

    company = models.ForeignKey('accounts.Company', models.CASCADE, related_name='+', verbose_name='公司')
    action = models.CharField('动作', max_length=64, db_index=True)
    entity_type = models.CharField('实体类型', max_length=32)
    entity_id = models.PositiveBigIntegerField('实体ID')
    tech_pack = models.ForeignKey(TechPackVersion, models.SET_NULL, null=True, blank=True,
                                  related_name='audit_events', verbose_name='工艺版本')
    actor = models.ForeignKey('accounts.User', models.SET_NULL, null=True, blank=True,
                              related_name='+', verbose_name='操作人')
    actor_name = models.CharField('操作人快照', max_length=100, blank=True, default='')
    before = models.JSONField('变更前', null=True, blank=True)
    after = models.JSONField('变更后', null=True, blank=True)
    message = models.TextField('事实说明', blank=True, default='')

    class Meta:
        db_table = 'preproduction_audit_event'
        ordering = ['id']
        indexes = [models.Index(fields=['company', 'entity_type', 'entity_id'],
                                name='idx_pp_audit_entity')]
