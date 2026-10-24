"""生产管理后台。"""
from django.contrib import admin

from apps.production.models import Production, ProductionProcess


@admin.register(Production)
class ProductionAdmin(admin.ModelAdmin):
    list_display = ('id', 'company', 'bed_no', 'style', 'total_qty', 'plan_qty', 'order_type',
                    'is_locked', 'is_cut_done', 'created_at')
    search_fields = ('bed_no', 'style__style_no', 'style__name')
    list_filter = ('company', 'order_type', 'is_locked', 'is_cut_done')


@admin.register(ProductionProcess)
class ProductionProcessAdmin(admin.ModelAdmin):
    list_display = ('id', 'production', 'process', 'price', 'created_at')
    list_filter = ('production__company',)
