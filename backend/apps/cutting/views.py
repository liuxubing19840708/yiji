"""裁片下发/签收/取消 API；GET=cutting.view，建单和取消=cutting.manage。"""
from functools import wraps

from rest_framework.exceptions import NotFound as DRFNotFound
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.mixins import CompanyContextMixin
from apps.core.pagination import DefaultPagination
from apps.manufacturing.exceptions import NotFound, ValidationError

from .models import CuttingHandover
from .serializers import HandoverCreateSerializer, HandoverSerializer
from .services import cancel_handover, create_handover, sign_handover


def domain_api(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except NotFound as exc:
            raise DRFNotFound(str(exc)) from None
        except ValidationError as exc:
            raise DRFValidationError(str(exc)) from None
    return wrapped


class HandoverBaseView(CompanyContextMixin, APIView):
    def handover_queryset(self):
        return (CuttingHandover.objects.filter(company=self.company)
                .select_related('ticket', 'source_node__stage', 'target_node__stage',
                                'receiver', 'created_by', 'signed_by', 'cancelled_by'))


class HandoverListCreateView(HandoverBaseView):
    @domain_api
    def get(self, request):
        self.check_perm('cutting.view')
        queryset = self.handover_queryset()
        for parameter, field in [('status', 'status'), ('ticket_id', 'ticket_id'),
                                 ('receiver_id', 'receiver_id'), ('source_node_id', 'source_node_id'),
                                 ('target_node_id', 'target_node_id')]:
            value = request.query_params.get(parameter)
            if value:
                queryset = queryset.filter(**{field: value})
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = HandoverSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @domain_api
    def post(self, request):
        self.check_perm('cutting.manage')
        serializer = HandoverCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        handover = create_handover(self.company, created_by=request.user,
                                   **serializer.validated_data)
        return Response(HandoverSerializer(handover).data, status=201)


class HandoverDetailView(HandoverBaseView):
    @domain_api
    def get(self, request, pk):
        self.check_perm('cutting.view')
        handover = self.handover_queryset().filter(pk=pk).first()
        if handover is None:
            raise DRFNotFound('裁片下发单不存在')
        return Response(HandoverSerializer(handover).data)


class HandoverSignView(HandoverBaseView):
    @domain_api
    def post(self, request, pk):
        handover = sign_handover(self.company, pk, actor=request.user)
        return Response(HandoverSerializer(handover).data)


class HandoverCancelView(HandoverBaseView):
    @domain_api
    def post(self, request, pk):
        self.check_perm('cutting.manage')
        handover = cancel_handover(self.company, pk, actor=request.user)
        return Response(HandoverSerializer(handover).data)
