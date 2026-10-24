"""薪酬结算后台管理。"""
from django.contrib import admin

from apps.salary.models import SalaryAdjust, SettleApply, Settlement


@admin.register(Settlement)
class SettlementAdmin(admin.ModelAdmin):
    list_display = ('id', 'company', 'user', 'period_start', 'period_end',
                    'employee_count', 'amount', 'status', 'created_by')
    list_filter = ('status', 'company')
    search_fields = ('user__real_name', 'user__phone', 'user__username')


@admin.register(SalaryAdjust)
class SalaryAdjustAdmin(admin.ModelAdmin):
    list_display = ('id', 'company', 'user', 'amount', 'reason', 'operator')
    list_filter = ('company',)
    search_fields = ('user__real_name', 'user__phone', 'reason')


@admin.register(SettleApply)
class SettleApplyAdmin(admin.ModelAdmin):
    list_display = ('id', 'company', 'applicant', 'user', 'period_start',
                    'period_end', 'amount', 'status', 'reviewer', 'paid_by')
    list_filter = ('status', 'company')
    search_fields = ('applicant__real_name', 'applicant__phone',
                     'user__real_name')
