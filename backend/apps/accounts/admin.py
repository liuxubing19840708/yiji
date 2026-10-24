"""后台管理：账号模块模型注册。"""
from django.contrib import admin

from .models import Company, Post, SMSCode, User, UserCompany


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('id', 'phone', 'username', 'real_name', 'gender', 'openid', 'date_joined')
    search_fields = ('phone', 'username', 'real_name', 'openid')
    list_filter = ('gender',)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'company_no', 'city', 'capacity', 'creator', 'created_at')
    search_fields = ('name', 'company_no')


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ('id', 'company', 'name', 'is_admin', 'is_system', 'created_at')
    list_filter = ('is_admin', 'is_system')
    search_fields = ('name',)


@admin.register(UserCompany)
class UserCompanyAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'company', 'post', 'status', 'is_pinned',
                    'is_suspended', 'hired_date', 'last_used_at')
    list_filter = ('status', 'is_suspended')
    search_fields = ('user__phone', 'user__real_name', 'company__name')


@admin.register(SMSCode)
class SMSCodeAdmin(admin.ModelAdmin):
    list_display = ('id', 'phone', 'code', 'expires_at', 'used_at', 'created_at')
    search_fields = ('phone',)
