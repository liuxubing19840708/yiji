"""产前管理 API；所有写动作委托 services，不在视图复制状态机。"""
from django.http import Http404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.mixins import CompanyContextMixin
from apps.orders.models import Order

from .models import BriefingTarget, CraftTemplate, ReviewIssue, SampleTask, TechPackVersion
from .serializers import (
    ApprovalSerializer, AuditEventSerializer, BriefingConfirmationSerializer,
    BriefingReceiptSerializer, BriefingTargetSerializer, CraftTemplateSerializer,
    ReviewIssueSerializer, SampleRecordSerializer, SampleTaskSerializer, TechPackSerializer,
)
from . import services as svc


class BaseView(CompanyContextMixin, APIView):
    def order(self, pk):
        order = Order.objects.filter(pk=pk, company=self.company).first()
        if order is None:
            raise Http404('订单不存在')
        return order

    def pack(self, pk):
        pack = (TechPackVersion.objects.filter(pk=pk, company=self.company)
                .select_related('order', 'style').prefetch_related(
                    'sample_tasks__records', 'review_issues', 'briefing_targets__receipt',
                    'briefing_confirmations__confirmed_by', 'approvals__decided_by').first())
        if pack is None:
            raise Http404('工艺版本不存在')
        return pack

    def child(self, model, pk):
        obj = model.objects.filter(pk=pk, company=self.company).select_related('tech_pack').first()
        if obj is None:
            raise Http404('产前记录不存在')
        return obj


class CraftTemplateListCreateView(BaseView):
    def get(self, request):
        templates = svc.list_craft_templates(
            self.user_company, request.query_params.get('kind', ''))
        return Response(CraftTemplateSerializer(templates, many=True).data)

    def post(self, request):
        template = svc.save_craft_template(self.user_company, request.data)
        return Response(CraftTemplateSerializer(template).data, status=201)


class CraftTemplateDetailView(BaseView):
    def get_object(self, pk):
        template = CraftTemplate.objects.filter(pk=pk, company=self.company).first()
        if template is None:
            raise Http404('公司模板不存在')
        return template

    def put(self, request, pk):
        template = svc.save_craft_template(
            self.user_company, request.data, self.get_object(pk))
        return Response(CraftTemplateSerializer(template).data)

    def delete(self, request, pk):
        svc.delete_craft_template(self.user_company, self.get_object(pk))
        return Response(status=204)


class TechPackListCreateView(BaseView):
    def get(self, request):
        self.check_perm(svc.VIEW)
        qs = (TechPackVersion.objects.filter(company=self.company)
              .select_related('order', 'style').prefetch_related(
                  'sample_tasks__records', 'review_issues', 'briefing_targets__receipt', 'approvals'))
        if request.query_params.get('order_id'):
            qs = qs.filter(order_id=request.query_params['order_id'])
        return Response(TechPackSerializer(qs, many=True).data)

    def post(self, request):
        order = self.order(request.data.get('order'))
        return Response(TechPackSerializer(
            svc.create_tech_pack(self.user_company, order, request.data)).data, status=201)


class TechPackDetailView(BaseView):
    def get(self, request, pk):
        self.check_perm(svc.VIEW)
        return Response(TechPackSerializer(self.pack(pk)).data)

    def put(self, request, pk):
        return Response(TechPackSerializer(
            svc.update_tech_pack(self.user_company, self.pack(pk), request.data)).data)

    def delete(self, request, pk):
        svc.delete_tech_pack(self.user_company, self.pack(pk))
        return Response(status=204)


class SampleTaskListCreateView(BaseView):
    def get(self, request, pack_id):
        self.check_perm(svc.VIEW)
        return Response(SampleTaskSerializer(self.pack(pack_id).sample_tasks.all(), many=True).data)

    def post(self, request, pack_id):
        task = svc.create_sample_task(self.user_company, self.pack(pack_id), request.data)
        return Response(SampleTaskSerializer(task).data, status=201)


class SampleRecordCreateView(BaseView):
    def post(self, request, pk):
        task = self.child(SampleTask, pk)
        record = svc.record_sample_result(self.user_company, task, request.data.get('result'),
                                          request.data.get('note', ''),
                                          request.data.get('attachments', []))
        return Response(SampleRecordSerializer(record).data, status=201)


class ReviewIssueListCreateView(BaseView):
    def get(self, request, pack_id):
        self.check_perm(svc.VIEW)
        return Response(ReviewIssueSerializer(self.pack(pack_id).review_issues.all(), many=True).data)

    def post(self, request, pack_id):
        issue = svc.create_review_issue(self.user_company, self.pack(pack_id), request.data)
        return Response(ReviewIssueSerializer(issue).data, status=201)


class ReviewIssueActionView(BaseView):
    def put(self, request, pk):
        issue = self.child(ReviewIssue, pk)
        action = request.data.get('action')
        if action == 'rectify':
            issue = svc.rectify_review_issue(self.user_company, issue, request.data.get('rectification', ''))
        elif action == 'resolve':
            issue = svc.resolve_review_issue(self.user_company, issue, request.data.get('resolution', ''))
        elif action == 'reopen':
            issue = svc.reopen_review_issue(self.user_company, issue, request.data.get('reason', ''))
        elif action == 'review':
            close = request.data.get('close')
            if not isinstance(close, bool):
                from rest_framework.exceptions import ValidationError
                raise ValidationError('close 必须为布尔值')
            issue = svc.review_issue(self.user_company, issue, close,
                                     request.data.get('review_note', ''))
        else:
            from rest_framework.exceptions import ValidationError
            raise ValidationError('action 仅支持 rectify/review/resolve/reopen')
        return Response(ReviewIssueSerializer(issue).data)


class BriefingTargetListCreateView(BaseView):
    def get(self, request, pack_id):
        self.check_perm(svc.VIEW)
        return Response(BriefingTargetSerializer(self.pack(pack_id).briefing_targets.all(), many=True).data)

    def post(self, request, pack_id):
        target = svc.add_briefing_target(self.user_company, self.pack(pack_id),
                                         request.data.get('employee'), request.data.get('note', ''))
        return Response(BriefingTargetSerializer(target).data, status=201)


class BriefingSignView(BaseView):
    def post(self, request, pk):
        target = BriefingTarget.objects.filter(pk=pk, company=self.company).first()
        if target is None:
            raise Http404('交底对象不存在')
        receipt = svc.sign_briefing(self.user_company, target, request.data.get('remark', ''))
        return Response(BriefingReceiptSerializer(receipt).data, status=201)


class BriefingConfirmationView(BaseView):
    def post(self, request, pack_id):
        confirmation = svc.confirm_briefing(
            self.user_company, self.pack(pack_id), request.data.get('note', ''))
        return Response(BriefingConfirmationSerializer(confirmation).data, status=201)


class ApprovalDecisionView(BaseView):
    def post(self, request, pk):
        action = request.data.get('action')
        if action not in ('approve', 'reject'):
            from rest_framework.exceptions import ValidationError
            raise ValidationError('action 仅支持 approve/reject')
        approval = svc.decide_approval(self.user_company, self.pack(pk), action == 'approve',
                                     request.data.get('reason', ''))
        return Response(ApprovalSerializer(approval).data, status=201)


class OrderApprovalStatusView(BaseView):
    def get(self, request, order_id):
        self.check_perm(svc.VIEW)
        order = self.order(order_id)
        approval = svc.get_active_approval(order)
        pack = TechPackVersion.objects.filter(order=order, is_current=True).first()
        problems = svc.readiness(pack) if pack else ['尚未创建工艺资料']
        previous_approval = bool(pack and pack.approvals.filter(
            status__in=['approved', 'invalidated']).exists())
        if approval:
            overall_status = 'approved'
        elif previous_approval:
            overall_status = 'reconfirm'
        elif problems:
            overall_status = 'draft'
        else:
            overall_status = 'pending_approval'
        status_display = {
            'draft': '待完善', 'pending_approval': '待审批',
            'approved': '已通过', 'reconfirm': '需重新确认',
        }[overall_status]
        latest_task = (pack.sample_tasks.order_by('-id').first() if pack else None)
        latest_sample = (latest_task.records.order_by('-occurred_at', '-id').first()
                         if latest_task else None)
        briefing = svc.current_briefing_confirmation(pack)
        return Response({
            'approved': approval is not None,
            'approval': ApprovalSerializer(approval).data if approval else None,
            'problems': problems,
            'status': overall_status,
            'status_display': status_display,
            'missing_items': problems,
            'current_sample': SampleRecordSerializer(latest_sample).data if latest_sample else None,
            'unresolved_issue_count': (pack.review_issues.exclude(status='closed').count()
                                       if pack else 0),
            'briefing_confirmation': (BriefingConfirmationSerializer(briefing).data
                                      if briefing else None),
            'has_previous_approval': previous_approval,
        })


class AuditEventListView(BaseView):
    def get(self, request, pack_id):
        self.check_perm(svc.VIEW)
        pack = self.pack(pack_id)
        return Response(AuditEventSerializer(pack.audit_events.all(), many=True).data)
