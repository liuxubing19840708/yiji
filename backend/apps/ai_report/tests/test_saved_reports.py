# -*- coding: utf-8 -*-
"""私有保存报表：所有权、实时权限、规范化和执行审计。"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Post, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.ai_report.models import AiQueryExecution, SavedReport


class SavedReportApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username='saved-owner', phone='13900008201', password='test-only')
        cls.company, _ = create_company_with_owner(cls.owner, '保存报表A厂')
        plain_post = Post.objects.create(company=cls.company, name='保存普工', permissions=[])
        order_post = Post.objects.create(
            company=cls.company, name='保存跟单', permissions=['orders.manage'])
        cls.colleague = User.objects.create_user(
            username='saved-colleague', phone='13900008202', password='test-only')
        cls.analyst = User.objects.create_user(
            username='saved-analyst', phone='13900008203', password='test-only')
        UserCompany.objects.create(
            user=cls.colleague, company=cls.company, post=plain_post,
            status=UserCompany.Status.ACTIVE)
        cls.analyst_link = UserCompany.objects.create(
            user=cls.analyst, company=cls.company, post=order_post,
            status=UserCompany.Status.ACTIVE)
        cls.other_owner = User.objects.create_user(
            username='saved-other', phone='13900008204', password='test-only')
        cls.other_company, _ = create_company_with_owner(cls.other_owner, '保存报表B厂')

    @staticmethod
    def plan(**patch):
        value = {
            'dataset': 'orders',
            'metrics': ['order_count'],
            'dimensions': ['status'],
            'filters': [],
            'order_by': [{'field': 'order_count', 'direction': 'desc'}],
            'limit': 100,
            'include_deleted': False,
            'presentation': 'table',
        }
        value.update(patch)
        return value

    def client_for(self, user, company=None):
        client = APIClient()
        client.force_authenticate(user)
        client.credentials(HTTP_X_COMPANY_ID=str((company or self.company).id))
        return client

    def create(self, user, name='我的订单报表', plan=None, expected=201, company=None):
        response = self.client_for(user, company).post('/api/ai/report/saved', {
            'name': name, 'plan': plan or self.plan(),
        }, format='json')
        self.assertEqual(response.status_code, expected, response.content)
        return response

    def test_private_crud_normalizes_plan_and_executes_with_audit(self):
        created = self.create(self.owner)
        report_id = created.data['id']
        self.assertEqual(created.data['plan']['metrics'], [{
            'key': 'order_count', 'aggregation': 'count',
        }])
        self.assertEqual(created.data['dataset'], 'orders')

        listed = self.client_for(self.owner).get('/api/ai/report/saved')
        self.assertEqual([item['id'] for item in listed.data['results']], [report_id])
        detail = self.client_for(self.owner).get(f'/api/ai/report/saved/{report_id}')
        self.assertEqual(detail.data['name'], '我的订单报表')

        renamed = self.client_for(self.owner).patch(
            f'/api/ai/report/saved/{report_id}', {'name': '改名后的订单报表'}, format='json')
        self.assertEqual(renamed.status_code, 200, renamed.content)
        self.assertEqual(renamed.data['plan']['dataset'], 'orders')
        self.create(self.owner, name='改名后的订单报表', expected=400)

        executed = self.client_for(self.owner).post(
            f'/api/ai/report/saved/{report_id}/execute', {}, format='json')
        self.assertEqual(executed.status_code, 200, executed.content)
        self.assertEqual(executed.data['saved_report']['id'], report_id)
        execution = AiQueryExecution.objects.get(id=executed.data['execution_id'])
        self.assertEqual(execution.status, 'success')
        self.assertEqual(execution.company, self.company)
        self.assertEqual(execution.user, self.owner)
        self.assertEqual(execution.plan['metrics'][0]['key'], 'order_count')

        deleted = self.client_for(self.owner).delete(f'/api/ai/report/saved/{report_id}')
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client_for(self.owner).get(
            f'/api/ai/report/saved/{report_id}').status_code, 404)

    def test_owner_and_company_isolation_returns_404(self):
        report_id = self.create(self.owner).data['id']
        for user, company in ((self.colleague, self.company),
                              (self.other_owner, self.other_company)):
            with self.subTest(user=user.username):
                client = self.client_for(user, company)
                self.assertEqual(client.get(
                    f'/api/ai/report/saved/{report_id}').status_code, 404)
                self.assertEqual(client.patch(
                    f'/api/ai/report/saved/{report_id}', {'name': '越权改名'},
                    format='json').status_code, 404)
                self.assertEqual(client.post(
                    f'/api/ai/report/saved/{report_id}/execute', {},
                    format='json').status_code, 404)
                self.assertEqual(client.delete(
                    f'/api/ai/report/saved/{report_id}').status_code, 404)
        self.assertTrue(SavedReport.objects.filter(id=report_id).exists())
        self.assertEqual(self.create(
            self.other_owner, company=self.other_company).data['name'], '我的订单报表')

    def test_permission_revocation_blocks_execution_but_allows_delete(self):
        report_id = self.create(self.analyst).data['id']
        post = self.analyst_link.post
        post.permissions = []
        post.save(update_fields=['permissions'])

        denied = self.client_for(self.analyst).post(
            f'/api/ai/report/saved/{report_id}/execute', {}, format='json')
        self.assertEqual(denied.status_code, 403, denied.content)
        self.assertEqual(AiQueryExecution.objects.latest('id').status, 'denied')
        updated = self.client_for(self.analyst).patch(
            f'/api/ai/report/saved/{report_id}', {'plan': self.plan()}, format='json')
        self.assertEqual(updated.status_code, 403)
        self.create(self.colleague, expected=403)
        self.assertEqual(self.client_for(self.analyst).delete(
            f'/api/ai/report/saved/{report_id}').status_code, 204)

    def test_invalid_saved_plan_is_rejected(self):
        invalid_plans = [
            self.plan(company_id=self.other_company.id),
            self.plan(dataset='missing'),
            self.plan(dimensions=['order__customer__name']),
            self.plan(metrics=[]),
        ]
        for plan in invalid_plans:
            with self.subTest(plan=plan):
                self.create(self.owner, plan=plan, expected=400)
        self.create(self.owner, name='', expected=400)
        response = self.client_for(self.owner).post(
            '/api/ai/report/saved', [], format='json')
        self.assertEqual(response.status_code, 400)
        self.create(self.colleague, plan=self.plan(
            dataset='piecework_records', metrics=['registered_qty'], dimensions=[],
            order_by=[], include_deleted=True), expected=403)
        self.assertFalse(SavedReport.objects.exists())

    def share(self, report_id, visibility, user=None, user_ids=None, post_ids=None,
              expected=200):
        response = self.client_for(user or self.owner).post(
            f'/api/ai/report/saved/{report_id}/share', {
                'visibility': visibility,
                'user_ids': user_ids or [],
                'post_ids': post_ids or [],
            }, format='json')
        self.assertEqual(response.status_code, expected, response.content)
        return response

    def test_company_share_and_recipient_cannot_manage(self):
        report_id = self.create(self.owner).data['id']
        shared = self.share(report_id, 'company')
        self.assertEqual(shared.data['visibility'], 'company')
        self.assertIsNotNone(shared.data['shared_at'])

        listed = self.client_for(self.analyst).get('/api/ai/report/saved')
        self.assertEqual([item['id'] for item in listed.data['results']], [report_id])
        item = listed.data['results'][0]
        self.assertFalse(item['is_owner'])
        self.assertNotIn('shares', item)
        executed = self.client_for(self.analyst).post(
            f'/api/ai/report/saved/{report_id}/execute', {}, format='json')
        self.assertEqual(executed.status_code, 200, executed.content)
        for method in ('patch', 'delete'):
            response = getattr(self.client_for(self.analyst), method)(
                f'/api/ai/report/saved/{report_id}',
                {'name': '接收者越权改名'} if method == 'patch' else None,
                format='json' if method == 'patch' else None)
            self.assertEqual(response.status_code, 404, response.content)
        self.share(report_id, 'private', user=self.analyst, expected=404)

    def test_selected_user_post_revocation_and_filter_confidentiality(self):
        secret = '机密客户筛选值'
        report_id = self.create(self.owner, plan=self.plan(filters=[{
            'field': 'customer_name', 'operator': 'contains', 'value': secret,
        }])).data['id']
        self.share(report_id, 'selected', user_ids=[self.colleague.id])
        client = self.client_for(self.colleague)
        self.assertEqual(client.get('/api/ai/report/saved').data['results'], [])
        self.assertEqual(client.get(f'/api/ai/report/saved/{report_id}').status_code, 404)
        self.assertNotIn(secret, str(client.get('/api/ai/report/saved').data))

        post = UserCompany.objects.get(user=self.colleague, company=self.company).post
        post.permissions = ['orders.manage']
        post.save(update_fields=['permissions'])
        listed = client.get('/api/ai/report/saved').data['results']
        self.assertEqual([item['id'] for item in listed], [report_id])
        post.permissions = []
        post.save(update_fields=['permissions'])
        self.assertEqual(client.get('/api/ai/report/saved').data['results'], [])
        denied = client.post(f'/api/ai/report/saved/{report_id}/execute', {}, format='json')
        self.assertEqual(denied.status_code, 403, denied.content)
        self.assertEqual(AiQueryExecution.objects.latest('id').status, 'denied')

        self.share(report_id, 'selected', post_ids=[self.analyst_link.post_id])
        self.assertEqual(len(self.client_for(self.analyst).get(
            '/api/ai/report/saved').data['results']), 1)
        self.share(report_id, 'private')
        self.assertEqual(self.client_for(self.analyst).get(
            f'/api/ai/report/saved/{report_id}').status_code, 404)

    def test_share_permission_targets_and_revoke_without_permission(self):
        report_id = self.create(self.owner).data['id']
        self.share(report_id, 'selected', user_ids=[self.other_owner.id], expected=400)
        self.share(report_id, 'selected', expected=400)
        self.share(report_id, 'company', user_ids=[self.colleague.id], expected=400)
        self.share(report_id, 'missing', expected=400)

        owner_link = UserCompany.objects.get(user=self.owner, company=self.company)
        owner_link.post.is_admin = False
        owner_link.post.permissions = ['orders.manage']
        owner_link.post.save(update_fields=['is_admin', 'permissions'])
        self.share(report_id, 'company', expected=403)
        targets = self.client_for(self.owner).get('/api/ai/report/saved/targets')
        self.assertEqual(targets.status_code, 403, targets.content)

        owner_link.post.permissions = ['orders.manage', 'ai.share']
        owner_link.post.save(update_fields=['permissions'])
        targets = self.client_for(self.owner).get('/api/ai/report/saved/targets')
        self.assertEqual(targets.status_code, 200, targets.content)
        self.assertIn(self.colleague.id, [item['id'] for item in targets.data['users']])
        self.share(report_id, 'company')
        owner_link.post.permissions = ['orders.manage']
        owner_link.post.save(update_fields=['permissions'])
        revoked = self.share(report_id, 'private')
        self.assertEqual(revoked.data['visibility'], 'private')
        self.assertIsNone(revoked.data['shared_at'])
