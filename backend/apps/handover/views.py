"""通用交接 API；全部查询限定 company，不开放交接 PATCH/DELETE。"""
from rest_framework import status
from rest_framework.exceptions import NotFound as DRFNotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.mixins import CompanyContextMixin
from apps.core.pagination import DefaultPagination

from .exceptions import IdempotencyError, NotFound, ValidationError
from .models import Handover, HandoverKind, HandoverPolicy
from .serializers import (HandoverCreateSerializer, HandoverPolicyInputSerializer,
                          HandoverPolicySerializer, HandoverReassignSerializer,
                          HandoverRejectSerializer, HandoverReceiveSerializer, HandoverSerializer)
from . import services as svc
from .timeout_services import attach_timeout_info


class HandoverBaseView(CompanyContextMixin, APIView):
    def handle_exception(self, exc):
        if isinstance(exc, NotFound):
            return Response({'detail': str(exc)}, status=status.HTTP_404_NOT_FOUND)
        if isinstance(exc, IdempotencyError):
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        if isinstance(exc, ValidationError):
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return super().handle_exception(exc)

    def handover_queryset(self):
        return (Handover.objects.filter(company=self.company)
                .select_related('source_node__stage', 'target_node__stage', 'receiver',
                                'created_by', 'received_by')
                .prefetch_related('items__ticket', 'events__actor', 'events__actual_user'))

    def handover_or_404(self, pk):
        handover = self.handover_queryset().filter(pk=pk).first()
        if handover is None:
            raise DRFNotFound('交接单不存在')
        attach_timeout_info([handover])
        return handover


class PolicyListCreateView(HandoverBaseView):
    def get(self, request):
        self.check_perm('handover.view')
        policies = list(HandoverPolicy.objects.filter(company=self.company)
                        .select_related('updated_by'))
        if not any(row.kind == HandoverKind.CUTTING and row.source_node_id is None
                   and row.target_node_id is None for row in policies):
            policies.append(HandoverPolicy(
                company=self.company, kind=HandoverKind.CUTTING,
                mode=HandoverPolicy.Mode.OFF))
        return Response(HandoverPolicySerializer(policies, many=True).data)

    def post(self, request):
        self.check_perm('handover.manage')
        form = HandoverPolicyInputSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        policy = svc.save_policy(self.company, form.validated_data, actor=request.user)
        return Response(HandoverPolicySerializer(policy).data, status=status.HTTP_201_CREATED)


class PolicyDetailView(HandoverBaseView):
    def put(self, request, pk):
        self.check_perm('handover.manage')
        policy = HandoverPolicy.objects.filter(company=self.company, pk=pk).first()
        if policy is None:
            raise DRFNotFound('交接策略不存在')
        form = HandoverPolicyInputSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        policy = svc.save_policy(self.company, form.validated_data, actor=request.user, policy=policy)
        return Response(HandoverPolicySerializer(policy).data)


class HandoverListCreateView(HandoverBaseView):
    def get(self, request):
        self.check_perm('handover.view')
        box = request.query_params.get('box', 'mine')
        if box not in ('mine', 'created'):
            raise ValidationError('box 仅支持 mine 或 created')
        queryset = self.handover_queryset()
        if box == 'created':
            queryset = queryset.filter(created_by=request.user)
        else:
            from django.db.models import Q
            queryset = queryset.filter(
                Q(receiver=request.user) | Q(received_by=request.user)
                | Q(receiver__isnull=True, target_group_no=self.user_company.group_no))
        for parameter in ('status', 'batch_no', 'kind'):
            value = request.query_params.get(parameter)
            if value:
                queryset = queryset.filter(**{parameter: value})
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        attach_timeout_info(page or [])
        return paginator.get_paginated_response(HandoverSerializer(page, many=True).data)

    def post(self, request):
        self.check_perm('handover.create')
        form = HandoverCreateSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        handovers = svc.create_handovers(self.company, created_by=request.user, **data)
        attach_timeout_info(handovers)
        return Response({'batch_no': handovers[0].batch_no,
                         'items': HandoverSerializer(handovers, many=True).data},
                        status=status.HTTP_201_CREATED)


class HandoverDetailView(HandoverBaseView):
    def get(self, request, pk):
        self.check_perm('handover.view')
        return Response(HandoverSerializer(self.handover_or_404(pk)).data)


class HandoverReceiveView(HandoverBaseView):
    def post(self, request, pk):
        # 服务同时校验 receive 权限及指定人/目标组资格。
        form = HandoverReceiveSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        handover = svc.receive_handover(self.company, pk, actor=request.user, **data)
        return Response(HandoverSerializer(self.handover_or_404(pk)).data)


class HandoverRejectView(HandoverBaseView):
    def post(self, request, pk):
        form = HandoverRejectSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        handover = svc.reject_handover(self.company, pk, actor=request.user, **form.validated_data)
        return Response(HandoverSerializer(self.handover_or_404(pk)).data)


class HandoverReassignView(HandoverBaseView):
    def post(self, request, pk):
        self.check_perm('handover.manage')
        form = HandoverReassignSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        handover = svc.reassign_handover(self.company, pk, actor=request.user, **form.validated_data)
        return Response(HandoverSerializer(self.handover_or_404(pk)).data)


class HandoverBatchView(HandoverBaseView):
    def get(self, request, batch_no):
        self.check_perm('handover.view')
        handovers = list(self.handover_queryset().filter(batch_no=batch_no).order_by('id'))
        attach_timeout_info(handovers)
        if not handovers:
            raise DRFNotFound('交接批次不存在')
        summary = {
            'handover_count': len(handovers),
            'received_count': sum(row.status == Handover.Status.RECEIVED for row in handovers),
            'pending_count': sum(row.status == Handover.Status.PENDING for row in handovers),
            'rejected_count': sum(row.status == Handover.Status.REJECTED for row in handovers),
            'total_quantity': sum(row.total_quantity for row in handovers),
            'received_quantity': sum(row.received_quantity for row in handovers),
        }
        return Response({'batch_no': batch_no,
                         'items': HandoverSerializer(handovers, many=True).data,
                         'summary': summary})
