"""技术管理模型：物料主档（Material）+ 版式BOM（Bom/BomItem）。

P1 tech 域（2026-10-24 P1设计决策表 A1-A4/B1-B5 + 《服装ERP全模块设计方案》§3.1）：
  - 全表继承 BaseModel（软删 + created_at/updated_at）；
  - 唯一约束一律 condition=Q(is_deleted=False)（软删后允许同码/同版本复用，订单域惯例）；
  - 所有 FK on_delete=PROTECT（仓库侧惯例：被引用的业务档案不可物理删除）；
  - 单位黑洞防线：BomItem.unit = Material 主单位快照，qty_req 一律折算主单位计；
  - 换算参数（weight_gsm/width_cm）缺失时接口层拦截提示（清单 A 组落点），
    物料录了副单位（unit_alt）即声明需要双单位换算，此时克重/幅宽必填。
"""
from decimal import Decimal

from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class Material(BaseModel):
    """物料主档：分类码-流水码编码，同公司唯一（软删防撞）。

    category 四类：fabric面料/lining里料/accessory辅料/packing包装；
    unit 主单位（针织公斤、梭织米为主流，A1：物料级可配属性容纳例外）；
    unit_alt 副单位（公斤↔米换算=克重×幅宽折算，录了副单位必须补克重+幅宽）；
    shrink_rate 缩率 DECIMAL(5,2)（A4：独立维护计入定额）；
    legacy_code 旧编码对照（E1：兼容过渡）；attrs JSON 扩展（成分/色号/fabric_type 等）。
    """

    class Category(models.TextChoices):
        FABRIC = 'fabric', '面料'
        LINING = 'lining', '里料'
        ACCESSORY = 'accessory', '辅料'
        PACKING = 'packing', '包装'

    class Status(models.TextChoices):
        ACTIVE = 'active', '启用'
        DISABLED = 'disabled', '停用'

    company = models.ForeignKey('accounts.Company', verbose_name='公司',
                                on_delete=models.CASCADE, related_name='tech_materials')
    code = models.CharField('物料编码', max_length=64, db_index=True)
    name = models.CharField('品名', max_length=128)
    category = models.CharField('分类', max_length=16,
                                choices=Category.choices, default=Category.FABRIC)
    unit = models.CharField('主单位', max_length=16)
    unit_alt = models.CharField('副单位', max_length=16, blank=True, default='',
                                help_text='录了副单位即声明双单位换算，克重+幅宽必填')
    weight_gsm = models.DecimalField('克重(g/m²)', max_digits=10, decimal_places=2,
                                     null=True, blank=True)
    width_cm = models.DecimalField('幅宽(cm)', max_digits=10, decimal_places=2,
                                   null=True, blank=True)
    shrink_rate = models.DecimalField('缩率%', max_digits=5, decimal_places=2,
                                      null=True, blank=True,
                                      help_text='A4：独立维护计入定额，定额=净用量×(1+缩率)×(1+损耗)')
    price_reference = models.DecimalField('参考价', max_digits=12, decimal_places=3,
                                          null=True, blank=True)
    # D19 拍板（2026-10-24）：批次单价单位用户可选（物料级缺省）；
    # 空=按主单位。单价录入按此单位报数，存储一律换算为主单位规范化值
    # （公斤↔米=克重×幅宽折算，见 warehouse.services.convert_price_to_main_unit）
    class PriceUnit(models.TextChoices):
        MAIN = '', '按主单位'
        KG = 'kg', '元/公斤'
        M = 'm', '元/米'

    price_unit = models.CharField('单价单位', max_length=2, blank=True, default='',
                                  choices=PriceUnit.choices, null=True,
                                  help_text='物料级缺省单价单位（D19）；空=按主单位，'
                                            '存储值恒为主单位规范化')
    # P2.5 对齐华遨「安全库存预警/自动采购建议」（2026-11-02）：主单位安全水位，
    # 需求汇总的补货口径 qty_reorder = max(0, 需求-库存-在途+安全库存)；
    # 0=不设水位（行不因安全库存触发补货）
    safety_stock = models.DecimalField('安全库存', max_digits=12, decimal_places=2,
                                       default=Decimal('0'),
                                       help_text='主单位安全水位/补货点；0=不设')

    status = models.CharField('状态', max_length=16, choices=Status.choices,
                              default=Status.ACTIVE)
    legacy_code = models.CharField('旧编码', max_length=64, blank=True, default='',
                                   help_text='旧编码对照（E1），兼容过渡期检索')
    attrs = models.JSONField('扩展属性', default=dict, blank=True,
                             help_text='成分/色号/fabric_type(woven/knit) 等扩展')

    class Meta:
        db_table = 'tech_material'
        verbose_name = '物料主档'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'code'],
                                    condition=Q(is_deleted=False),
                                    name='uq_tech_material_company_code'),
        ]
        indexes = [
            models.Index(fields=['company', 'category'], name='idx_tmat_co_category'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.code} {self.name}'

    @property
    def conversion_ready(self) -> bool:
        """双单位换算参数是否齐备（克重×幅宽，公斤↔米折算依据）。"""
        return self.weight_gsm is not None and self.width_cm is not None

    def default_loss_rate(self) -> Decimal:
        """BomItem.loss_rate 品类默认值（B5：梭织6/针织8/辅料0.4或10，可覆盖）。

        判断依据：先看 attrs.fabric_type（woven/knit），无则按 category 兜底——
        面料/里料梭织 6、针织（attrs 声明）8、辅料 0.4（高频辅料 10 由 attrs.loss_rate
        显式覆盖，接口层传 loss_rate 即可）。
        """
        attrs = self.attrs or {}
        if 'loss_rate' in attrs:
            try:
                return Decimal(str(attrs['loss_rate']))
            except Exception:
                pass
        if self.category == self.Category.ACCESSORY:
            return Decimal('0.4')
        if (self.category in (self.Category.FABRIC, self.Category.LINING)
                and str(attrs.get('fabric_type', '')).lower() == 'knit'):
            return Decimal('8.0')
        return Decimal('6.0')


class Bom(BaseModel):
    """版式BOM（金蝶式：一个款式一个BOM；版本=lite：改版另存新version）。

    status：draft(草稿) → active(生效)；改版 fork 后旧版自动 deactivated 留痕；
    一个款式仅一个 active（DB 部分唯一约束 + activate 事务内降级旧版双保险）。
    P1 不做版本 diff/呆滞料分析/取替代料（清单 B4：一单锁定一版由此支持）。
    """

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        ACTIVE = 'active', '生效'
        DEACTIVATED = 'deactivated', '已停用'

    company = models.ForeignKey('accounts.Company', verbose_name='公司',
                                on_delete=models.CASCADE, related_name='tech_boms')
    style = models.ForeignKey('catalog.Style', verbose_name='款式',
                              on_delete=models.PROTECT, related_name='tech_boms')
    version = models.PositiveIntegerField('版本', default=1)
    status = models.CharField('状态', max_length=16, choices=Status.choices,
                              default=Status.DRAFT)
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'tech_bom'
        verbose_name = '版式BOM'
        verbose_name_plural = verbose_name
        ordering = ['-id']
        constraints = [
            models.UniqueConstraint(fields=['company', 'style', 'version'],
                                    condition=Q(is_deleted=False),
                                    name='uq_tech_bom_company_style_version'),
            # 单 active：同公司同款式仅一条生效行（软删行不占位）
            models.UniqueConstraint(fields=['company', 'style'],
                                    condition=Q(status='active', is_deleted=False),
                                    name='uq_tech_bom_company_style_active'),
        ]
        indexes = [
            models.Index(fields=['company', 'status'], name='idx_tbom_co_status'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.style_id} v{self.version}({self.status})'


class BomItem(BaseModel):
    """BOM 明细行：物料 × 颜色 × 尺码（空串=通码，MVP 不做色码维 BOM）。

    unit = Material 主单位快照（防物料改单位后历史行漂移；qty_req 一律折算主单位计）；
    单耗与损耗率必须分列（B5），损耗后用量 = unit_usage × (1+loss_rate/100)
    【计算属性，不落库】；UNIQUE(bom, material, color, size) 防重复行。
    """

    bom = models.ForeignKey(Bom, verbose_name='BOM',
                            on_delete=models.PROTECT, related_name='items')
    material = models.ForeignKey(Material, verbose_name='物料',
                                 on_delete=models.PROTECT, related_name='bom_items')
    kind = models.CharField('类别', max_length=16, choices=[
        ('main', '面料'), ('lining', '里料'), ('accessory', '辅料')],
        default='main')
    unit = models.CharField('单位(主单位快照)', max_length=16)
    color = models.CharField('颜色', max_length=64, blank=True, default='',
                             help_text='空串=通码')
    size = models.CharField('尺码', max_length=32, blank=True, default='',
                            help_text='空串=通码')
    unit_usage = models.DecimalField('单耗(净用量)', max_digits=12, decimal_places=4)
    loss_rate = models.DecimalField('损耗率%', max_digits=5, decimal_places=2,
                                    default=Decimal('0'))
    position = models.CharField('部位', max_length=64, blank=True, default='')
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'tech_bom_item'
        verbose_name = 'BOM明细'
        verbose_name_plural = verbose_name
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['bom', 'material', 'color', 'size'],
                                    condition=Q(is_deleted=False),
                                    name='uq_tech_bom_item_bom_mat_color_size'),
        ]

    def __str__(self):
        return f'{self.bom_id}:{self.material_id} x{self.unit_usage}'

    @property
    def gross_usage(self) -> Decimal:
        """损耗后用量（计算属性，不落库）：单耗 × (1+损耗率/100)。"""
        return (self.unit_usage * (Decimal('100') + self.loss_rate)
                / Decimal('100')).quantize(Decimal('0.0001'))
