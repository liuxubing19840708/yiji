# -*- coding: utf-8 -*-
"""固定 Planner 评测集必须全过，且运行期间不访问真实 GLM。"""
from django.test import TestCase

from apps.accounts.models import Post, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.ai_report.planner_evaluation import evaluate_planner


class PlannerEvaluationTests(TestCase):
    def test_fixed_offline_evaluation_cases(self):
        owner = User.objects.create_user(
            username='planner-eval-owner', phone='13900008701', password='test-only')
        company, _ = create_company_with_owner(owner, 'Planner评测厂')
        post = Post.objects.create(
            company=company, name='订单采购评测',
            permissions=['orders.manage', 'warehouse.manage'])
        link = UserCompany.objects.get(user=owner, company=company)
        link.post = post
        link.save(update_fields=['post'])
        result = evaluate_planner(link)
        self.assertEqual(result['passed'], result['total'], result)
        self.assertGreaterEqual(result['total'], 10)
