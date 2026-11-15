"""运营支撑：公司设置（KV）、站内通知、操作日志。"""
from django.db import models
from django.db.models import Q

from apps.accounts.models import Company, User
from apps.core.models import BaseModel


class CompanySetting(BaseModel):
    """公司系统设置（KV 存储，含基础开关与分阶段业务门禁）。"""

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='settings', verbose_name='公司', db_index=True)
    key = models.CharField('设置键', max_length=64, db_index=True)
    value = models.CharField('设置值', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'company_setting'
        verbose_name = '公司设置'
        verbose_name_plural = '公司设置'
        constraints = [
            models.UniqueConstraint(fields=['company', 'key'], condition=Q(is_deleted=False),
                                    name='uq_company_setting_company_key'),
        ]

    # 设置及默认值（开关项 '1' 开 / '0' 关；门禁项 off / warn / strict）
    DEFAULTS = {
        'auto_cut_done': '0',               # 默认裁完：编菲后自动置裁完
        'revoke_current_month_only': '1',   # 只能撤销当月登记
        'hide_scan_price': '0',             # 隐藏扫菲单价
        'filter_zero_tickets': '1',         # 过滤数量为 0 的菲票
        'show_cut_total': '1',              # 显示裁货总数
        'hide_style_total_price': '1',      # 隐藏款式总单价
        'hide_employee_salary': '0',        # 隐藏员工工资
        'forbid_unapproved_register': '0',  # 未审核禁止登记
        'default_counter_on': '0',          # 默认开启计数
        'show_salary_style_image': '1',     # 显示薪酬款式图片
        'default_print_mode': 'cloud',      # 默认打印方式：cloud 云打印 / bluetooth 蓝牙
        'cloud_printer': '',                # 云打印机信息（JSON：sn/key 等）
        'print_label_style': '{}',          # 菲票标签模板（JSON：LabelStyle，'{}' 用默认经典款）
        'print_label_offsets': '{}',        # 每台打印机对位偏移（JSON：打印名→mm，公司级同步）
        'personalized_notify': '1',         # 个性化通知
        'preproduction_gate_mode': 'off',   # 产前大货门禁：off 兼容 / warn 警告 / strict 拦截
        'manufacturing_gate_mode': 'off',   # 节点数量门禁：off 影子 / warn 警告 / strict 拦截
    }

    @classmethod
    def ensure_defaults(cls, company):
        """为公司补齐全部设置项（幂等，公司创建/编菲等场景调用）。"""
        existing = cls.objects.filter(company=company).values_list('key', flat=True)
        missing = [k for k in cls.DEFAULTS if k not in existing]
        cls.objects.bulk_create([cls(company=company, key=k, value=cls.DEFAULTS[k]) for k in missing])

    @classmethod
    def get_map(cls, company) -> dict:
        """返回 key -> value 的完整映射（缺失项用默认值兜底，不落库）。"""
        settings = {s.key: s.value for s in cls.objects.filter(company=company)}
        return {k: settings.get(k, v) for k, v in cls.DEFAULTS.items()}

    @classmethod
    def set_values(cls, company, mapping: dict) -> list:
        """批量写入设置，返回实际变更的 key 列表；未知 key 抛 ValueError。"""
        changed = []
        for key, value in mapping.items():
            if key not in cls.DEFAULTS:
                raise ValueError(f'未知设置项：{key}')
            obj, created = cls.objects.update_or_create(company=company, key=key, defaults={'value': str(value)})
            changed.append(key)
        return changed

    @classmethod
    def get_bool(cls, company, key) -> bool:
        return cls.get_map(company).get(key) == '1'


class Notification(BaseModel):
    """站内通知（个人通知：加入申请/审批通过/离职提醒等）。"""

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='notifications', verbose_name='公司', db_index=True)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='notifications', verbose_name='接收人', db_index=True)
    code = models.CharField('通知类型', max_length=32, db_index=True)
    title = models.CharField('标题', max_length=64)
    message = models.TextField('内容')  # 服务端插值完成后落库，不存模板
    is_read = models.BooleanField('已读', default=False, db_index=True)

    class Meta:
        db_table = 'notification'
        verbose_name = '站内通知'
        verbose_name_plural = '站内通知'
        ordering = ['-created_at']


class AuditLog(BaseModel):
    """操作日志（文档 §十四 五类业务 + 其他；敏感操作必落一条）。"""

    class Type(models.TextChoices):
        PRICE = 'price', '修改价格'
        ASSIST = 'assist', '协助登记'
        QTY = 'qty', '修改裁床数'
        PROCESS = 'process', '修改工序'
        REVOKE = 'revoke', '撤销登记'
        OTHER = 'other', '其他'

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name='audit_logs', verbose_name='公司', db_index=True)
    type = models.CharField('日志类型', max_length=16, choices=Type.choices, db_index=True)
    bed_no = models.CharField('床次', max_length=32, blank=True, default='')
    style_no = models.CharField('款号', max_length=64, blank=True, default='')
    bundle_no = models.CharField('扎号', max_length=32, blank=True, default='')
    target_user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='被操作员工')
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='操作人')
    before = models.JSONField('变更前', null=True, blank=True)
    after = models.JSONField('变更后', null=True, blank=True)
    message = models.TextField('展示文案')  # 服务端插值完成后的文案，直接展示

    class Meta:
        db_table = 'audit_log'
        verbose_name = '操作日志'
        verbose_name_plural = '操作日志'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'type', 'created_at'], name='idx_audit_company_type_time'),
        ]
