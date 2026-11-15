"""质检视图（GAP-1）：登记/列表、收货、完成、删除、导出。

权限（2 段式）：GET/导出 = quality.view；登记/收货/完成/删除 = quality.manage。
状态机：rework →（收货）received →（完成）done；done 终态禁改。
"""
from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import UserCompany
from apps.catalog.models import Ticket
from apps.core.export import xlsx_response
from apps.core.mixins import CompanyContextMixin
from apps.core.pagination import DefaultPagination
from apps.core.permissions import has_perm
from apps.ops.services import audit
from apps.quality.models import QualityInspection, QualityRecord
from apps.quality.serializers import (
    QualityFinishSerializer, QualityInspectionConfirmSerializer, QualityInspectionSerializer,
    QualityReceiveSerializer, QualityRecordSerializer,
)
from apps.quality.services import confirm_inspection, create_inspection
from apps.manufacturing.exceptions import NotFound as ManufacturingNotFound
from apps.manufacturing.exceptions import ValidationError as ManufacturingValidationError


def _require_manage(self):
    if not has_perm(self.user_company, 'quality.manage'):
        raise PermissionDenied('无质检管理权限')


def _require_view(self):
    if not has_perm(self.user_company, 'quality.view'):
        raise PermissionDenied('无质检查看权限')


class QualityRecordListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET 质检列表（quality.view）；POST 登记返修（quality.manage）。"""

    serializer_class = QualityRecordSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        _require_view(self)
        qs = (QualityRecord.objects.filter(company=self.company)
              .select_related('ticket', 'ticket__production', 'ticket__production__style',
                              'process', 'producer', 'inspector'))
        params = self.request.query_params
        status = params.get('status')
        if status:
            qs = qs.filter(status=status)
        ticket_id = params.get('ticket_id')
        if ticket_id:
            qs = qs.filter(ticket_id=ticket_id)
        producer_id = params.get('producer_id')
        if producer_id:
            qs = qs.filter(producer_id=producer_id)
        bed_no = params.get('bed_no')
        if bed_no:
            qs = qs.filter(ticket__bed_no=bed_no)
        return qs

    def perform_create(self, serializer):
        _require_manage(self)
        ticket = serializer.validated_data['ticket']
        if ticket.company_id != self.company.id:
            raise ValidationError({'ticket': '菲票不属于当前公司'})
        process = serializer.validated_data.get('process')
        if process is not None and process.company_id != self.company.id:
            raise ValidationError({'process': '工序不属于当前公司'})
        producer = serializer.validated_data.get('producer')
        if producer is not None and not UserCompany.objects.filter(
                user=producer, company=self.company,
                status=UserCompany.Status.ACTIVE).exists():
            raise ValidationError({'producer': '返修工人须为该公司在职成员'})
        # 同菲票同工序在途返修去重（软删行先物理清理，见 records._purge_deleted 同法）
        QualityRecord.all_objects.filter(
            is_deleted=True, company=self.company, ticket=ticket,
            status=QualityRecord.Status.REWORK,
            process_id=process.id if process else None).delete()
        try:
            serializer.save(
                company=self.company,
                inspector=self.user_company.user,
                inspected_at=timezone.now())
        except IntegrityError:
            raise ValidationError('该菲票该工序已有在途返修记录')


class QualityRecordDetailView(CompanyContextMixin, APIView):
    """GET 详情；PUT 动作收货/完成；DELETE 删除（均 quality.manage）。"""

    def get_object(self, pk):
        return get_object_or_404(
            QualityRecord.objects.select_related(
                'ticket', 'process', 'producer', 'inspector'),
            company=self.company, pk=pk)

    def get(self, request, pk):
        _require_view(self)
        return Response(QualityRecordSerializer(self.get_object(pk)).data)

    def put(self, request, pk):
        _require_manage(self)
        rec = self.get_object(pk)
        action = request.data.get('action')
        if action == 'receive':
            return self._receive(request, rec)
        if action == 'finish':
            return self._finish(request, rec)
        raise ValidationError({'action': '仅支持 receive（收货）/ finish（完成）'})

    def _receive(self, request, rec):
        if rec.status != QualityRecord.Status.REWORK:
            raise ValidationError(f'仅「返修中」可收货，当前：{rec.get_status_display()}')
        ser = QualityReceiveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        rq = ser.validated_data['received_qty']
        if rq > rec.defect_qty:
            raise ValidationError(f'收回数量不能超过返修数量 {rec.defect_qty}')
        producer_id = ser.validated_data.get('producer')
        producer = None
        if producer_id:
            try:
                producer = UserCompany.objects.get(
                    user_id=producer_id, company=self.company,
                    status=UserCompany.Status.ACTIVE).user
            except UserCompany.DoesNotExist:
                raise ValidationError({'producer': '返修工人须为该公司在职成员'})
        before = rec.status
        rec.status = QualityRecord.Status.RECEIVED
        rec.received_qty = rq
        rec.received_at = timezone.now()
        if producer:
            rec.producer = producer
        rec.save(update_fields=['status', 'received_qty', 'received_at', 'producer', 'updated_at'])
        t = rec.ticket
        audit(self.company, 'other', operator=self.user_company.user, target_user=rec.producer,
              bed_no=t.bed_no, style_no='', bundle_no=t.bundle_no,
              before={'status': before}, after={'status': rec.status},
              message=f'{t.bed_no} 第{t.bundle_no}扎返修收货：收回 {rq}/{rec.defect_qty} 件')
        return Response(QualityRecordSerializer(rec).data)

    def _finish(self, request, rec):
        if rec.status != QualityRecord.Status.RECEIVED:
            raise ValidationError(f'仅「已收回」可完成，当前：{rec.get_status_display()}')
        ser = QualityFinishSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        before = rec.status
        rec.status = QualityRecord.Status.DONE
        if ser.validated_data.get('remark'):
            rec.remark = ser.validated_data['remark']
        rec.save(update_fields=['status', 'remark', 'updated_at'])
        t = rec.ticket
        audit(self.company, 'other', operator=self.user_company.user, target_user=rec.producer,
              bed_no=t.bed_no, style_no='', bundle_no=t.bundle_no,
              before={'status': before}, after={'status': rec.status},
              message=f'{t.bed_no} 第{t.bundle_no}扎返修完成')
        return Response(QualityRecordSerializer(rec).data)

    def delete(self, request, pk):
        _require_manage(self)
        rec = self.get_object(pk)
        if rec.status == QualityRecord.Status.DONE:
            raise PermissionDenied('已完成的质检记录不可删除')
        t = rec.ticket
        rec.delete()  # 软删
        audit(self.company, 'other', operator=self.user_company.user,
              bed_no=t.bed_no, style_no='', bundle_no=t.bundle_no,
              message=f'删除质检记录：{t.bed_no} 第{t.bundle_no}扎 返修 {rec.defect_qty} 件（{rec.reason}）')
        return Response(status=204)


class QualityExportView(CompanyContextMixin, APIView):
    """GET 质检导出 xlsx（quality.view）；行数上限 20000，与列表同过滤。"""

    MAX_ROWS = 20000
    HEADERS = ['质检时间', '款号', '床次', '扎号', '颜色', '尺码', '返修工序',
               '返修数量', '收回数量', '返修原因', '状态', '返修工人', '质检人', '备注']

    def get(self, request):
        _require_view(self)
        qs = (QualityRecord.objects.filter(company=self.company)
              .select_related('ticket', 'ticket__production__style', 'process',
                              'producer', 'inspector'))
        params = request.query_params
        if params.get('status'):
            qs = qs.filter(status=params['status'])
        if params.get('ticket_id'):
            qs = qs.filter(ticket_id=params['ticket_id'])
        if params.get('producer_id'):
            qs = qs.filter(producer_id=params['producer_id'])
        if params.get('bed_no'):
            qs = qs.filter(ticket__bed_no=params['bed_no'])
        rows = []
        for r in qs[:self.MAX_ROWS]:
            t = r.ticket
            rows.append([
                timezone.localtime(r.inspected_at).strftime('%Y-%m-%d %H:%M'),
                getattr(getattr(t.production, 'style', None), 'style_no', '') or '',
                t.bed_no, t.bundle_no, t.color, t.size,
                r.process.name if r.process else '',
                r.defect_qty, r.received_qty, r.reason,
                r.get_status_display(),
                r.producer.real_name if r.producer else '',
                r.inspector.real_name if r.inspector else '',
                r.remark,
            ])
        return xlsx_response(self.HEADERS, rows, filename='质检记录')


def _raise_inspection_api_error(error):
    if isinstance(error, ManufacturingNotFound):
        raise NotFound(str(error))
    raise ValidationError(str(error))


class QualityInspectionListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """节点检验单：GET quality.view，POST quality.manage。"""

    serializer_class = QualityInspectionSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        _require_view(self)
        qs = (QualityInspection.objects.filter(company=self.company)
              .select_related('ticket', 'node__stage', 'rework_node__stage', 'inspector'))
        params = self.request.query_params
        for param, field in (('status', 'status'), ('inspection_type', 'inspection_type'),
                             ('ticket_id', 'ticket_id'), ('node_id', 'node_id')):
            if params.get(param):
                qs = qs.filter(**{field: params[param]})
        return qs

    def perform_create(self, serializer):
        _require_manage(self)
        try:
            serializer.instance = create_inspection(
                self.company, inspector=self.user_company.user, **serializer.validated_data)
        except (ManufacturingNotFound, ManufacturingValidationError) as error:
            _raise_inspection_api_error(error)


class QualityInspectionDetailView(CompanyContextMixin, generics.RetrieveAPIView):
    serializer_class = QualityInspectionSerializer

    def get_queryset(self):
        _require_view(self)
        return (QualityInspection.objects.filter(company=self.company)
                .select_related('ticket', 'node__stage', 'rework_node__stage', 'inspector'))


class QualityInspectionConfirmView(CompanyContextMixin, APIView):
    """确认检验单；同一 request_id 重试返回原单，不重复写数量事件。"""

    def post(self, request, pk):
        _require_manage(self)
        ser = QualityInspectionConfirmSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        inspection = get_object_or_404(QualityInspection, company=self.company, pk=pk)
        try:
            inspection = confirm_inspection(
                self.company, inspection, request_id=ser.validated_data['request_id'],
                operator=self.user_company.user)
        except (ManufacturingNotFound, ManufacturingValidationError) as error:
            _raise_inspection_api_error(error)
        return Response(QualityInspectionSerializer(inspection).data)
