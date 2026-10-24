"""运营支撑接口：系统设置（管理员批量读写）、操作日志（筛选）、站内通知（本人 + 标记已读）。"""
from django.db.models import Q
from django.http import Http404
from django.utils.dateparse import parse_date
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.mixins import CompanyContextMixin
from apps.core.pagination import DefaultPagination
from apps.ops.models import AuditLog, CompanySetting, Notification
from apps.ops.serializers import AuditLogSerializer, NotificationSerializer, SettingSerializer
from apps.ops.services import audit


class SettingView(CompanyContextMixin, APIView):
    """GET 返回全部设置（员工端读展示开关）；PUT 管理员批量更新。"""

    def get(self, request):
        return Response(CompanySetting.get_map(self.company))

    def put(self, request):
        # Plan A：系统设置走 settings.manage 点（管理员岗位恒通过）
        self.check_perm('settings.manage')
        ser = SettingSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        before = CompanySetting.get_map(self.company)
        changed = CompanySetting.set_values(self.company, ser.validated_data)
        if changed:
            detail = '、'.join(f'{k}: {before[k]} → {ser.validated_data[k]}' for k in changed)
            audit(self.company, AuditLog.Type.OTHER, operator=request.user,
                  before={k: before[k] for k in changed},
                  after={k: ser.validated_data[k] for k in changed},
                  message=f'{request.user.real_name} 修改系统设置：{detail}')
        return Response(CompanySetting.get_map(self.company))


class AuditLogListView(CompanyContextMixin, generics.ListAPIView):
    """操作日志列表。筛选：type（price/assist/qty/process/revoke/other）、
    date=YYYY-MM-DD（单日）或 date_from/date_to（区间）、keyword。
    scope=mine：员工视角「与我相关的变更」——target_user 或 operator 是本人
    的记录（痛点 #3，调研 §8.3）；缺省仍为管理员全量视图。"""
    serializer_class = AuditLogSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        queryset = AuditLog.objects.filter(company=self.company)
        if self.request.query_params.get('scope') == 'mine':
            queryset = queryset.filter(
                Q(target_user=self.request.user) | Q(operator=self.request.user))
        else:
            # Plan A：全量日志走 logs.view 点（scope=mine 员工视角不设门槛，不变）
            self.check_perm('logs.view')
        log_type = self.request.query_params.get('type')
        if log_type:
            queryset = queryset.filter(type=log_type)
        date = parse_date(self.request.query_params.get('date', ''))
        date_from = parse_date(self.request.query_params.get('date_from', ''))
        date_to = parse_date(self.request.query_params.get('date_to', ''))
        if date:
            queryset = queryset.filter(created_at__date=date)
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)
        keyword = self.request.query_params.get('keyword', '').strip()
        if keyword:
            queryset = queryset.filter(
                Q(style_no__icontains=keyword) | Q(bed_no__icontains=keyword)
                | Q(bundle_no__icontains=keyword) | Q(message__icontains=keyword))
        return queryset


class NotificationListView(CompanyContextMixin, generics.ListAPIView):
    """本人的站内通知列表。筛选：is_read=0/1、code。"""
    serializer_class = NotificationSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        queryset = Notification.objects.filter(company=self.company, user=self.request.user)
        is_read = self.request.query_params.get('is_read')
        if is_read in ('0', '1'):
            queryset = queryset.filter(is_read=(is_read == '1'))
        code = self.request.query_params.get('code')
        if code:
            queryset = queryset.filter(code=code)
        return queryset


class NotificationReadView(CompanyContextMixin, APIView):
    """POST /notifications/<pk>/read 标记单条已读；POST /notifications/read-all 全部已读。"""

    def post(self, request, pk=None):
        if pk is None:
            Notification.objects.filter(
                company=self.company, user=self.request.user, is_read=False).update(is_read=True)
            return Response({'message': '已全部标记为已读'})
        updated = Notification.objects.filter(
            company=self.company, user=self.request.user, id=pk).update(is_read=True)
        if not updated:
            raise Http404('通知不存在')
        return Response({'message': '已标记为已读'})
