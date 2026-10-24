from django.contrib import admin

from apps.ops.models import AuditLog, CompanySetting, Notification


@admin.register(CompanySetting)
class CompanySettingAdmin(admin.ModelAdmin):
    list_display = ['id', 'company', 'key', 'value', 'updated_at']
    list_filter = ['key']
    search_fields = ['company__name', 'key', 'value']


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['id', 'company', 'user', 'code', 'title', 'is_read', 'created_at']
    list_filter = ['code', 'is_read']
    search_fields = ['company__name', 'user__real_name', 'user__phone', 'title', 'message']


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['id', 'company', 'type', 'bed_no', 'style_no', 'bundle_no',
                    'operator', 'target_user', 'message', 'created_at']
    list_filter = ['type']
    search_fields = ['company__name', 'style_no', 'bed_no', 'bundle_no', 'message']
