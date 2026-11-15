# -*- coding: utf-8 -*-
"""保存报表 CRUD、公司内分享与执行；读取和执行均按接收者实时鉴权。"""
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Post, User, UserCompany
from apps.core.mixins import CompanyContextMixin

from .catalog import can_use, get_dataset
from .models import SavedReport, SavedReportShare
from .plan_schema import validate_plan
from .plan_views import PlanExecutionMixin


def _display_name(user):
    return user.real_name or user.username


def serialize_saved(instance, user):
    is_owner = instance.user_id == user.id
    value = {
        'id': instance.id,
        'name': instance.name,
        'dataset': instance.dataset,
        'plan': instance.plan,
        'visibility': instance.visibility,
        'shared_at': instance.shared_at,
        'is_owner': is_owner,
        'can_manage': is_owner,
        'owner': {'id': instance.user_id, 'name': _display_name(instance.user)},
        'created_at': instance.created_at,
        'updated_at': instance.updated_at,
    }
    if is_owner:
        value['shares'] = [{
            'id': share.id,
            'user_id': share.user_id,
            'user_name': _display_name(share.user) if share.user else '',
            'post_id': share.post_id,
            'post_name': share.post.name if share.post else '',
        } for share in instance.shares.all()]
    return value


class SavedReportMixin(CompanyContextMixin):
    def get_queryset(self):
        """管理范围永远只属于所有者，不因公司共享而扩大。"""
        return SavedReport.objects.filter(company=self.company, user=self.request.user)

    def get_readable_queryset(self):
        readable = Q(user=self.request.user) | Q(visibility=SavedReport.Visibility.COMPANY)
        selected = Q(visibility=SavedReport.Visibility.SELECTED, shares__user=self.request.user)
        if self.user_company.post_id:
            selected |= Q(
                visibility=SavedReport.Visibility.SELECTED,
                shares__post_id=self.user_company.post_id)
        return SavedReport.objects.filter(company=self.company).filter(
            readable | selected).select_related('user').prefetch_related(
                'shares__user', 'shares__post').distinct()

    @staticmethod
    def shared_plan_allowed(instance, user_company):
        """接收者无权时连计划、名称和筛选值都不在共享列表下发。"""
        if instance.user_id == user_company.user_id:
            return True
        dataset = get_dataset(instance.dataset)
        if dataset is None or not can_use(user_company, dataset.permission):
            return False
        try:
            validate_plan(instance.plan, dataset, user_company, max_limit=500)
        except (PermissionDenied, ValidationError):
            return False
        return True

    @staticmethod
    def _get(queryset, pk):
        try:
            return queryset.get(pk=pk)
        except SavedReport.DoesNotExist:
            raise Http404

    def get_object(self, pk):
        return self._get(self.get_queryset(), pk)

    def get_readable_object(self, pk, check_plan=True):
        instance = self._get(self.get_readable_queryset(), pk)
        if check_plan and not self.shared_plan_allowed(instance, self.user_company):
            raise Http404
        return instance

    def normalized_values(self, data, instance=None):
        if not isinstance(data, dict):
            raise ValidationError({'detail': '请求体必须是 JSON 对象'})
        raw_name = data.get('name', instance.name if instance else '')
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValidationError({'name': '报表名称不能为空'})
        name = raw_name.strip()
        if len(name) > 80:
            raise ValidationError({'name': '报表名称不能超过 80 字'})

        raw_plan = data.get('plan', instance.plan if instance else None)
        raw_dataset = raw_plan.get('dataset') if isinstance(raw_plan, dict) else None
        if not isinstance(raw_dataset, str) or not raw_dataset:
            raise ValidationError({'plan': '必须指定 dataset'})
        dataset = get_dataset(raw_dataset)
        if dataset is None:
            raise ValidationError({'plan': f'不支持的数据集：{raw_dataset}'})
        if not can_use(self.user_company, dataset.permission):
            raise PermissionDenied(f'无权保存数据集：{dataset.label}')
        plan = validate_plan(raw_plan, dataset, self.user_company, max_limit=500)
        return name, plan

    def check_duplicate(self, name, instance=None):
        queryset = self.get_queryset().filter(name=name)
        if instance is not None:
            queryset = queryset.exclude(pk=instance.pk)
        if queryset.exists():
            raise ValidationError({'name': '同名报表已存在'})


class AiSavedReportListView(SavedReportMixin, APIView):
    def get(self, request):
        results = [
            serialize_saved(item, request.user) for item in self.get_readable_queryset()
            if self.shared_plan_allowed(item, self.user_company)
        ]
        return Response({'results': results})

    def post(self, request):
        if self.get_queryset().count() >= 100:
            raise ValidationError({'detail': '每个公司账号最多保存 100 张报表'})
        name, plan = self.normalized_values(request.data)
        self.check_duplicate(name)
        try:
            instance = SavedReport.objects.create(
                company=self.company, user=request.user, name=name,
                dataset=plan['dataset'], plan=plan)
        except IntegrityError:
            raise ValidationError({'name': '同名报表已存在'})
        instance = self.get_readable_queryset().get(pk=instance.pk)
        return Response(serialize_saved(instance, request.user), status=201)


class AiSavedReportDetailView(SavedReportMixin, APIView):
    def get(self, request, pk):
        return Response(serialize_saved(self.get_readable_object(pk), request.user))

    def patch(self, request, pk):
        instance = self.get_object(pk)
        name, plan = self.normalized_values(request.data, instance)
        self.check_duplicate(name, instance)
        instance.name = name
        instance.dataset = plan['dataset']
        instance.plan = plan
        try:
            instance.save(update_fields=['name', 'dataset', 'plan', 'updated_at'])
        except IntegrityError:
            raise ValidationError({'name': '同名报表已存在'})
        instance = self.get_readable_queryset().get(pk=instance.pk)
        return Response(serialize_saved(instance, request.user))

    def put(self, request, pk):
        return self.patch(request, pk)

    def delete(self, request, pk):
        self.get_object(pk).delete()
        return Response(status=204)


class AiSavedReportExecuteView(PlanExecutionMixin, SavedReportMixin, APIView):
    def post(self, request, pk):
        # 不提前用接收者权限隐藏对象，让统一执行链路留下 denied/invalid 审计。
        instance = self.get_readable_object(pk, check_plan=False)
        response = self.execute(request, 'preview', raw_plan_override=instance.plan)
        response.data['saved_report'] = {
            'id': instance.id, 'name': instance.name,
        }
        return response


class AiSavedReportShareView(SavedReportMixin, APIView):
    """事务内完整替换 ACL；所有者无需分享权限也可收回已有分享。"""

    @staticmethod
    def _ids(data, key):
        raw = data.get(key, [])
        if not isinstance(raw, list):
            raise ValidationError({key: '必须是 ID 数组'})
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
               for value in raw):
            raise ValidationError({key: '必须全部为有效正整数 ID'})
        if len(raw) != len(set(raw)):
            raise ValidationError({key: '不能包含重复 ID'})
        if len(raw) > 200:
            raise ValidationError({key: '每次最多指定 200 个目标'})
        return raw

    def post(self, request, pk):
        if not isinstance(request.data, dict):
            raise ValidationError({'detail': '请求体必须是 JSON 对象'})
        visibility = request.data.get('visibility')
        if visibility not in SavedReport.Visibility.values:
            raise ValidationError({'visibility': '仅支持 private/company/selected'})
        user_ids = self._ids(request.data, 'user_ids')
        post_ids = self._ids(request.data, 'post_ids')
        if visibility == SavedReport.Visibility.SELECTED and not user_ids and not post_ids:
            raise ValidationError({'detail': '指定分享至少选择一个成员或岗位'})
        if visibility != SavedReport.Visibility.SELECTED and (user_ids or post_ids):
            raise ValidationError({'detail': '仅指定分享可以包含成员或岗位'})
        if visibility != SavedReport.Visibility.PRIVATE:
            self.check_perm('ai.share')

        users = list(User.objects.filter(
            id__in=user_ids, is_active=True,
            company_links__company=self.company,
            company_links__status=UserCompany.Status.ACTIVE,
            company_links__is_deleted=False).distinct())
        posts = list(Post.objects.filter(
            id__in=post_ids, company=self.company, is_deleted=False))
        if len(users) != len(user_ids) or len(posts) != len(post_ids):
            raise ValidationError({'detail': '分享目标必须是本公司有效在职成员或岗位'})

        with transaction.atomic():
            instance = self._get(self.get_queryset().select_for_update(), pk)
            instance.visibility = visibility
            instance.shared_at = (
                timezone.now() if visibility != SavedReport.Visibility.PRIVATE else None)
            instance.save(update_fields=['visibility', 'shared_at', 'updated_at'])
            instance.shares.all().delete()
            SavedReportShare.objects.bulk_create(
                [SavedReportShare(report=instance, user=user) for user in users]
                + [SavedReportShare(report=instance, post=post) for post in posts])
        instance = self.get_readable_queryset().get(pk=instance.pk)
        return Response(serialize_saved(instance, request.user))


class AiSavedReportShareTargetsView(SavedReportMixin, APIView):
    def get(self, request):
        self.check_perm('ai.share')
        links = UserCompany.objects.filter(
            company=self.company, status=UserCompany.Status.ACTIVE,
            is_deleted=False, user__is_active=True).select_related('user', 'post').order_by(
                'user__real_name', 'user__username', 'id')
        posts = Post.objects.filter(company=self.company, is_deleted=False).order_by('name', 'id')
        return Response({
            'users': [{
                'id': link.user_id,
                'name': _display_name(link.user),
                'username': link.user.username,
                'post_id': link.post_id,
                'post_name': link.post.name if link.post else '',
            } for link in links],
            'posts': [{'id': post.id, 'name': post.name} for post in posts],
        })
