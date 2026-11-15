"""包装、成品入库和现货 API；读取需 view，写入/审核需 manage。"""
from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.exceptions import NotFound as DRFNotFound
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.mixins import CompanyContextMixin
from apps.manufacturing.exceptions import (NotFound as ManufacturingNotFound,
                                           ValidationError as ManufacturingValidationError)
from .exceptions import NotFound, ValidationError
from .models import FinishedInbound, PackingBox, PackingTask
from .serializers import (FinishedInboundCreateSerializer, FinishedInboundSerializer,
                          PackingBoxSerializer, PackingTaskCreateSerializer,
                          PackingTaskSerializer, RequestSerializer, ReverseRequestSerializer)
from .services import (approve_finished_inbound, approve_packing_task, create_finished_inbound,
                       create_packing_task, get_inventory, reverse_finished_inbound,
                       reverse_packing_task)


def _domain_call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (NotFound, ManufacturingNotFound) as exc:
        raise DRFNotFound(str(exc)) from None
    except (ValidationError, ManufacturingValidationError) as exc:
        raise DRFValidationError(str(exc)) from None
    except IntegrityError:
        raise DRFValidationError('单号、箱号或幂等号冲突') from None


class _FinishedGoodsView(CompanyContextMixin):
    def require_view(self):
        self.check_perm('finished_goods.view')

    def require_manage(self):
        self.check_perm('finished_goods.manage')


class PackingTaskListCreateView(_FinishedGoodsView, generics.ListCreateAPIView):
    pagination_class = None

    def get_serializer_class(self):
        return PackingTaskCreateSerializer if self.request.method == 'POST' else PackingTaskSerializer

    def get_queryset(self):
        self.require_view()
        queryset = (PackingTask.objects.filter(company=self.company)
                    .select_related('route', 'reverser').prefetch_related('boxes__lines__ticket'))
        params = self.request.query_params
        if params.get('route_id'):
            queryset = queryset.filter(route_id=params['route_id'])
        if params.get('status'):
            queryset = queryset.filter(status=params['status'])
        if params.get('task_no'):
            queryset = queryset.filter(task_no=params['task_no'])
        return queryset

    def create(self, request, *args, **kwargs):
        self.require_manage()
        serializer = PackingTaskCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = _domain_call(create_packing_task, self.company, operator=self.user_company.user,
                            **serializer.validated_data)
        return Response(PackingTaskSerializer(task).data, status=status.HTTP_201_CREATED)


class PackingTaskDetailView(_FinishedGoodsView, APIView):
    def get(self, request, pk):
        self.require_view()
        task = get_object_or_404(
            PackingTask.objects.select_related('route', 'reverser').prefetch_related('boxes__lines__ticket'),
            pk=pk, company=self.company)
        return Response(PackingTaskSerializer(task, context={'include_ledger': True}).data)


class PackingTaskApproveView(_FinishedGoodsView, APIView):
    def post(self, request, pk):
        self.require_manage()
        serializer = RequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = _domain_call(approve_packing_task, self.company, pk, request_id=serializer.validated_data[
            'request_id'], operator=self.user_company.user)
        return Response(PackingTaskSerializer(task).data)


class PackingTaskReverseView(_FinishedGoodsView, APIView):
    def post(self, request, pk):
        self.require_manage()
        serializer = ReverseRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = _domain_call(reverse_packing_task, self.company, pk,
                            operator=self.user_company.user, **serializer.validated_data)
        return Response(PackingTaskSerializer(task).data)


class PackingBoxListView(_FinishedGoodsView, generics.ListAPIView):
    serializer_class = PackingBoxSerializer
    pagination_class = None

    def get_queryset(self):
        self.require_view()
        queryset = (PackingBox.objects.filter(company=self.company)
                    .prefetch_related('lines__ticket'))
        params = self.request.query_params
        if params.get('box_no'):
            queryset = queryset.filter(box_no=params['box_no'])
        if params.get('task_id'):
            queryset = queryset.filter(task_id=params['task_id'])
        return queryset


class PackingBoxDetailView(_FinishedGoodsView, APIView):
    def get(self, request, pk):
        self.require_view()
        box = get_object_or_404(PackingBox.objects.prefetch_related('lines__ticket'),
                                pk=pk, company=self.company)
        return Response(PackingBoxSerializer(box).data)


class FinishedInboundListCreateView(_FinishedGoodsView, generics.ListCreateAPIView):
    pagination_class = None

    def get_serializer_class(self):
        return (FinishedInboundCreateSerializer if self.request.method == 'POST'
                else FinishedInboundSerializer)

    def get_queryset(self):
        self.require_view()
        queryset = (FinishedInbound.objects.filter(company=self.company)
                    .select_related('route', 'reverser').prefetch_related('lines__box', 'lines__ticket'))
        params = self.request.query_params
        if params.get('route_id'):
            queryset = queryset.filter(route_id=params['route_id'])
        if params.get('status'):
            queryset = queryset.filter(status=params['status'])
        if params.get('document_no'):
            queryset = queryset.filter(document_no=params['document_no'])
        return queryset

    def create(self, request, *args, **kwargs):
        self.require_manage()
        serializer = FinishedInboundCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document = _domain_call(create_finished_inbound, self.company,
                                operator=self.user_company.user, **serializer.validated_data)
        return Response(FinishedInboundSerializer(document).data, status=status.HTTP_201_CREATED)


class FinishedInboundDetailView(_FinishedGoodsView, APIView):
    def get(self, request, pk):
        self.require_view()
        document = get_object_or_404(
            FinishedInbound.objects.select_related('route', 'reverser').prefetch_related(
                'lines__box', 'lines__ticket'), pk=pk, company=self.company)
        return Response(FinishedInboundSerializer(document, context={'include_ledger': True}).data)


class FinishedInboundApproveView(_FinishedGoodsView, APIView):
    def post(self, request, pk):
        self.require_manage()
        serializer = RequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document = _domain_call(
            approve_finished_inbound, self.company, pk,
            request_id=serializer.validated_data['request_id'], operator=self.user_company.user)
        return Response(FinishedInboundSerializer(document).data)


class FinishedInboundReverseView(_FinishedGoodsView, APIView):
    def post(self, request, pk):
        self.require_manage()
        serializer = ReverseRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document = _domain_call(reverse_finished_inbound, self.company, pk,
                                operator=self.user_company.user, **serializer.validated_data)
        return Response(FinishedInboundSerializer(document).data)


class FinishedInventoryView(_FinishedGoodsView, APIView):
    def get(self, request):
        self.require_view()
        allowed = {'route_id', 'order_id', 'ticket_id', 'production_id', 'bed_no', 'color', 'size',
                   'include_zero'}
        filters = {key: request.query_params[key] for key in allowed if key in request.query_params}
        if 'include_zero' in filters:
            filters['include_zero'] = str(filters['include_zero']).lower() in ('1', 'true', 'yes')
        rows = _domain_call(get_inventory, self.company, **filters)
        return Response({'results': rows, 'total_available': sum(row['available'] for row in rows)})
