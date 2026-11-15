"""技术管理后台：物料主档 / 版式BOM（运营排查用，业务走 API）。"""
from django.contrib import admin

from apps.tech.models import Bom, BomItem, Material


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ('id', 'company', 'code', 'name', 'category', 'unit', 'unit_alt',
                    'weight_gsm', 'width_cm', 'shrink_rate', 'status', 'created_at')
    search_fields = ('code', 'name', 'legacy_code')
    list_filter = ('company', 'category', 'status')


@admin.register(Bom)
class BomAdmin(admin.ModelAdmin):
    list_display = ('id', 'company', 'style', 'version', 'status', 'remark', 'created_at')
    search_fields = ('style__style_no',)
    list_filter = ('company', 'status')


@admin.register(BomItem)
class BomItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'bom', 'material', 'kind', 'unit', 'color', 'size',
                    'unit_usage', 'loss_rate', 'created_at')
    list_filter = ('bom__company', 'kind')
