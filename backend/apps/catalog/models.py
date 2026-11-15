from decimal import Decimal

from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class CustomerGroup(BaseModel):
    """客户分组（§十二 客户管理）"""

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='customer_groups', verbose_name='公司')
    name = models.CharField('分组名称', max_length=64)

    class Meta:
        db_table = 'customer_group'
        verbose_name = '客户分组'
        verbose_name_plural = verbose_name
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'name'], condition=Q(is_deleted=False),
                                    name='uq_customer_group_company_name'),
        ]

    def __str__(self):
        return self.name


class Customer(BaseModel):
    """客户档案（§十二 客户管理）"""

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='customers', verbose_name='公司')
    customer_no = models.CharField('客户编号', max_length=32)
    group = models.ForeignKey(CustomerGroup, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='customers', verbose_name='客户分组')
    name = models.CharField('客户名称', max_length=128)
    phone = models.CharField('联系电话', max_length=20, blank=True, default='')
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'customer'
        verbose_name = '客户'
        verbose_name_plural = verbose_name
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'customer_no'], condition=Q(is_deleted=False),
                                    name='uq_customer_company_no'),
        ]

    def __str__(self):
        return f'{self.customer_no} {self.name}'


class Process(BaseModel):
    """工序档案（§九 工序管理）

    is_fixed=True 为固定工序：扫菲页默认选中的登记工序；
    其余为指定工序，通过员工/岗位指定后出现在工序选择器中。
    """

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='processes', verbose_name='公司')
    name = models.CharField('工序名称', max_length=64)
    default_price = models.DecimalField('默认单价', max_digits=10, decimal_places=3, default=0)
    is_fixed = models.BooleanField('固定工序', default=False)
    # 竞品对齐（2026-11-04 差距分析 GAP-8/GAP-6）：关键工序用于看板工序维度
    # 「关键工序」聚焦过滤；隐藏工价控制扫菲页是否对无 styles.price/salary.view
    # 权限者下发该工序单价（登记本身不受影响，登记走 _resolve_price 现价）。
    is_key = models.BooleanField('关键工序', default=False)
    hide_price = models.BooleanField('对工人隐藏工价', default=False)
    sort = models.PositiveIntegerField('排序', default=0)

    class Meta:
        db_table = 'process'
        verbose_name = '工序'
        verbose_name_plural = verbose_name
        ordering = ['sort', 'id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'name'], condition=Q(is_deleted=False),
                                    name='uq_process_company_name'),
        ]

    def __str__(self):
        return self.name


class Style(BaseModel):
    """款式档案（§五 款式管理）

    price 为款式总单价（各工序单价之和，保存工序配置时自动汇总），
    用于「隐藏款式总单价」等展示设置。
    """

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='styles', verbose_name='公司')
    style_no = models.CharField('款号', max_length=64)
    name = models.CharField('款式名称', max_length=128)
    price = models.DecimalField('款式总单价', max_digits=10, decimal_places=3, default=0)
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='styles', verbose_name='客户')
    image = models.CharField('款式图片地址', max_length=255, blank=True, default='')
    # 款式图片多图（2026-10-21 用户需求）：存 /media/ 相对路径数组，≤9 张；
    # image 恒等于首张（保存时同步），旧读方（订单 style_image/看板/列表缩略图）不用改
    images = models.JSONField('款式图片', default=list, blank=True)
    # 工艺说明 + 视频演示（2026-10-21 用户 8 项需求 #2）：craft_images 存 /media/ 相对路径数组
    craft_note = models.TextField('工艺说明', blank=True, default='')
    craft_images = models.JSONField('工艺图片', default=list, blank=True)
    video = models.CharField('工艺视频地址', max_length=255, blank=True, default='')
    # 面料（2026-10-21 用户需求）：款式级面料文本，建床时作默认面料（Production.fabric 选填带出）
    fabric = models.CharField('面料', max_length=100, blank=True, default='')
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'style'
        verbose_name = '款式'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'style_no'], condition=Q(is_deleted=False),
                                    name='uq_style_company_no'),
        ]

    def __str__(self):
        return f'{self.style_no} {self.name}'

    def recalc_price(self):
        """按工序配置汇总款式总单价"""
        total = sum((sp.price for sp in self.processes.all()), start=Decimal('0'))
        self.price = total
        self.save(update_fields=['price', 'updated_at'])
        return self.price


class ProcessTemplate(BaseModel):
    """工序模板（2026-10-21 用户 8 项需求 #1）：款式工序配置的复用预设。

    items 存 [{process_id, price}]，应用时由前端/款式保存逻辑展开成工序配置；
    name 公司内唯一（软删防撞：condition 只约束未删除行，与项目软删家族一致）。
    """

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='process_templates', verbose_name='公司')
    name = models.CharField('模板名称', max_length=50)
    items = models.JSONField('工序配置', default=list, blank=True,
                             help_text='[{process_id, price}]')

    class Meta:
        db_table = 'process_template'
        verbose_name = '工序模板'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'name'], condition=Q(is_deleted=False),
                                    name='uq_process_template_company_name'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.name}'


class StyleProcess(BaseModel):
    """款式-工序配置（§五 款式工序单价）"""

    style = models.ForeignKey(Style, on_delete=models.CASCADE,
                              related_name='processes', verbose_name='款式')
    process = models.ForeignKey(Process, on_delete=models.CASCADE,
                                related_name='style_processes', verbose_name='工序')
    price = models.DecimalField('工序单价', max_digits=10, decimal_places=3, default=0)
    sort = models.PositiveIntegerField('排序', default=0)
    # 跳序开关（2026-10-18 拍板）：False 时该扎必须先登记完 sort 更小的工序才能登本工序；
    # 默认 True 兼容存量数据与现状（全部允许跳序）
    allow_skip = models.BooleanField('允许跳序', default=True)

    class Meta:
        db_table = 'style_process'
        verbose_name = '款式工序'
        verbose_name_plural = verbose_name
        ordering = ['sort', 'id']
        constraints = [
            models.UniqueConstraint(fields=['style', 'process'], condition=Q(is_deleted=False),
                                    name='uq_style_process'),
        ]

    def __str__(self):
        return f'{self.style} - {self.process}'


class Ticket(BaseModel):
    """菲票（§六 编菲）

    qr_code 存 apps.core.utils.build_ticket_qr 产出的竖线分隔串，前端据此渲染二维码。
    (床次, 扎号) 在公司内唯一（§十九 规则1）；冲突时编菲端自动顺延。
    """

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='tickets', verbose_name='公司')
    production = models.ForeignKey('production.Production', on_delete=models.CASCADE,
                                   related_name='tickets', verbose_name='裁床订单')
    bed_no = models.CharField('床次', max_length=32)
    bundle_no = models.CharField('扎号', max_length=32)
    color = models.CharField('颜色', max_length=64, blank=True, default='')
    size = models.CharField('尺码', max_length=32, blank=True, default='')
    qty = models.PositiveIntegerField('数量')
    fabric = models.CharField('布料', max_length=64, blank=True, default='')
    piece_no = models.CharField('件号', max_length=32, blank=True, default='')
    qr_code = models.CharField('二维码内容', max_length=255)
    is_printed = models.BooleanField('已打印', default=False)
    print_count = models.IntegerField('打印次数', default=0)
    print_at = models.DateTimeField('最后打印时间', null=True, blank=True)

    class Meta:
        db_table = 'ticket'
        verbose_name = '菲票'
        verbose_name_plural = verbose_name
        ordering = ['bed_no', 'id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'bed_no', 'bundle_no'],
                                    condition=Q(is_deleted=False),
                                    name='uq_ticket_company_bed_bundle'),
        ]
        indexes = [
            models.Index(fields=['company', 'bed_no'], name='idx_ticket_company_bed'),
        ]

    def __str__(self):
        return f'{self.bed_no} 第{self.bundle_no}扎 {self.color} {self.size} x{self.qty}'


class TicketPrintLog(BaseModel):
    """打印记录（M4）：打印上报明细，逐票一条；print_key 幂等去重用。

    同一 print_key + 菲票只允许一条（条件唯一约束，print_key 为空的历史/兼容上报不参与约束）；
    is_reprint 取计数前的 is_printed 快照：首打 False、补打 True。
    """

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='ticket_print_logs', verbose_name='公司')
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE,
                               related_name='print_logs', verbose_name='菲票')
    operator = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True,
                                 blank=True, related_name='ticket_print_logs',
                                 verbose_name='操作人')
    print_key = models.CharField('打印幂等键', max_length=64, null=True, blank=True,
                                 db_index=True)
    is_reprint = models.BooleanField('补打', default=False)

    class Meta:
        db_table = 'ticket_print_log'
        verbose_name = '打印记录'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['print_key', 'ticket'],
                                    condition=Q(print_key__isnull=False),
                                    name='uniq_ticket_print_log_key_ticket'),
        ]

    def __str__(self):
        return f'{self.ticket_id} 第{self.id}次打印 key={self.print_key}'
