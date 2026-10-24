"""基础档案后台管理。"""
from django.contrib import admin

from apps.catalog.models import (
    Customer,
    CustomerGroup,
    Process,
    Style,
    StyleProcess,
    Ticket,
)


@admin.register(CustomerGroup)
class CustomerGroupAdmin(admin.ModelAdmin):
    list_display = ('id', 'company', 'name', 'created_at')
    search_fields = ('name',)
    list_filter = ('company',)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('id', 'company', 'customer_no', 'name', 'group', 'phone', 'created_at')
    search_fields = ('customer_no', 'name', 'phone')
    list_filter = ('company', 'group')


@admin.register(Process)
class ProcessAdmin(admin.ModelAdmin):
    list_display = ('id', 'company', 'name', 'default_price', 'is_fixed', 'sort', 'created_at')
    search_fields = ('name',)
    list_filter = ('company', 'is_fixed')


@admin.register(Style)
class StyleAdmin(admin.ModelAdmin):
    list_display = ('id', 'company', 'style_no', 'name', 'price', 'customer', 'created_at')
    search_fields = ('style_no', 'name')
    list_filter = ('company', 'customer')


@admin.register(StyleProcess)
class StyleProcessAdmin(admin.ModelAdmin):
    list_display = ('id', 'style', 'process', 'price', 'sort', 'created_at')
    list_filter = ('style__company',)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('id', 'company', 'bed_no', 'bundle_no', 'color', 'size', 'qty',
                    'is_printed', 'created_at')
    search_fields = ('bed_no', 'bundle_no', 'color', 'size')
    list_filter = ('company', 'bed_no', 'is_printed')
