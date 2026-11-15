"""技术管理序列化器：物料主档 / 版式BOM / BOM明细。

约定（对齐订单域）：
  - BOM 的 items 由视图层校验落库（防跨厂物料/同码重复行等业务规则），
    序列化器只做只读展示 + 轻量结构校验；
  - loss_rate 缺省时由视图按物料品类默认值补（B5：梭织6/针织8/辅料0.4，可显式覆盖）；
  - conversion_ready = 克重+幅宽齐备（双单位换算可用），列表/详情随行下发供前端提示。
"""
from rest_framework import serializers

from apps.catalog.models import Style
from apps.tech.models import Bom, BomItem, Material


class MaterialSerializer(serializers.ModelSerializer):
    conversion_ready = serializers.BooleanField(read_only=True)
    # P2.5 安全库存非负校验：负水位会让补货点口径虚增应补量（测试设计 X-SAFETY 发现）
    safety_stock = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0,
                                            required=False,
                                            error_messages={
                                                'min_value': '安全库存不能为负数'})

    class Meta:
        model = Material
        fields = ['id', 'code', 'name', 'category', 'unit', 'unit_alt',
                  'weight_gsm', 'width_cm', 'shrink_rate', 'price_reference',
                  'price_unit', 'safety_stock', 'status', 'legacy_code', 'attrs',
                  'conversion_ready', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'code': {'error_messages': {'blank': '请填写物料编码',
                                        'required': '请填写物料编码'}},
            'name': {'error_messages': {'blank': '请填写品名',
                                        'required': '请填写品名'}},
            'unit': {'error_messages': {'blank': '请填写主单位',
                                        'required': '请填写主单位'}},
        }


class BomItemSerializer(serializers.ModelSerializer):
    material_code = serializers.CharField(source='material.code', read_only=True)
    material_name = serializers.CharField(source='material.name', read_only=True)

    class Meta:
        model = BomItem
        fields = ['id', 'material', 'material_code', 'material_name', 'kind', 'unit',
                  'color', 'size', 'unit_usage', 'loss_rate', 'position', 'remark']
        read_only_fields = ['id', 'unit']


class BomSerializer(serializers.ModelSerializer):
    items = BomItemSerializer(many=True, read_only=True)
    # style 建单必传（缺失由视图层报「款式不存在」）；PUT 不传保持原款式（改版走 fork）
    style = serializers.PrimaryKeyRelatedField(
        queryset=Style.objects.all(), required=False, label='款式',
        error_messages={'invalid': '款式不存在', 'incorrect_type': '款式不存在',
                        'does_not_exist': '款式不存在'})
    style_no = serializers.CharField(source='style.style_no', read_only=True, default='')
    style_name = serializers.CharField(source='style.name', read_only=True, default='')
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Bom
        fields = ['id', 'style', 'style_no', 'style_name', 'version', 'status',
                  'remark', 'items', 'item_count', 'created_at', 'updated_at']
        read_only_fields = ['id', 'status', 'created_at', 'updated_at']

    def get_item_count(self, obj) -> int:
        return obj.items.count()
