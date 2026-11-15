# -*- coding: utf-8 -*-
"""异步导出：持久任务、显式 worker、私有下载、撤权与过期。"""
import tempfile
from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Post, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.ai_report.export_services import expire_old_exports, process_next_export
from apps.ai_report.models import AiExportTask, AiQueryExecution


class AsyncExportApiTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.settings_override = override_settings(
            AI_EXPORT_ROOT=cls.tempdir.name, AI_EXPORT_RETENTION_HOURS=24)
        cls.settings_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.settings_override.disable()
        cls.tempdir.cleanup()

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username='export-owner', phone='13900008601', password='test-only')
        cls.company, _ = create_company_with_owner(cls.owner, '异步导出A厂')
        cls.analyst = User.objects.create_user(
            username='export-analyst', phone='13900008602', password='test-only')
        cls.post = Post.objects.create(
            company=cls.company, name='导出分析', permissions=['orders.manage', 'ai.export'])
        UserCompany.objects.create(
            user=cls.analyst, company=cls.company, post=cls.post,
            status=UserCompany.Status.ACTIVE)
        cls.other = User.objects.create_user(
            username='export-other', phone='13900008603', password='test-only')
        cls.other_company, _ = create_company_with_owner(cls.other, '异步导出B厂')

    @staticmethod
    def plan():
        return {
            'dataset': 'orders',
            'metrics': [{'key': 'order_count', 'aggregation': 'count'}],
            'dimensions': ['status'], 'filters': [], 'order_by': [],
            'limit': 100, 'include_deleted': False, 'presentation': 'table',
        }

    def client_for(self, user, company=None):
        client = APIClient()
        client.force_authenticate(user)
        client.credentials(HTTP_X_COMPANY_ID=str((company or self.company).id))
        return client

    def create(self, user=None, expected=202):
        response = self.client_for(user or self.analyst).post(
            '/api/ai/report/exports', {'plan': self.plan()}, format='json')
        self.assertEqual(response.status_code, expected, response.content)
        return response

    def test_persistent_worker_success_download_and_isolation(self):
        task_id = self.create().data['id']
        task = AiExportTask.objects.get(id=task_id)
        self.assertEqual(task.status, 'pending')
        self.assertFalse(task.file_path)
        self.assertEqual(process_next_export(), task.id)
        task.refresh_from_db()
        self.assertEqual(task.status, 'success')
        self.assertEqual(task.execution.status, 'success')
        self.assertIsNotNone(task.expires_at)

        status = self.client_for(self.analyst).get(f'/api/ai/report/exports/{task_id}')
        self.assertEqual(status.status_code, 200, status.content)
        self.assertEqual(status.data['status'], 'success')
        downloaded = self.client_for(self.analyst).get(
            f'/api/ai/report/exports/{task_id}/download')
        self.assertEqual(downloaded.status_code, 200, downloaded.content if hasattr(downloaded, 'content') else '')
        self.assertTrue(b''.join(downloaded.streaming_content).startswith(b'PK'))
        self.assertEqual(downloaded['X-Ai-Execution-Id'], str(task.execution_id))

        other_client = self.client_for(self.other, self.other_company)
        self.assertEqual(other_client.get(f'/api/ai/report/exports/{task_id}').status_code, 404)
        self.assertEqual(other_client.get(
            f'/api/ai/report/exports/{task_id}/download').status_code, 404)
        self.assertEqual(other_client.get('/api/ai/report/exports').data['results'], [])
        self.assertEqual(process_next_export(), None)
        call_command('process_ai_exports', '--once', verbosity=0)

    def test_worker_rechecks_permission_and_never_auto_executes(self):
        task_id = self.create().data['id']
        self.post.permissions = ['orders.manage']
        self.post.save(update_fields=['permissions'])
        process_next_export()
        task = AiExportTask.objects.get(id=task_id)
        self.assertEqual(task.status, 'failed')
        self.assertEqual(task.error_code, 'permission_denied')
        self.assertEqual(AiQueryExecution.objects.latest('id').status, 'denied')
        self.assertFalse(task.file_path)

    def test_download_rechecks_data_permission_and_export_permission(self):
        task_id = self.create().data['id']
        process_next_export()
        self.post.permissions = ['ai.export']
        self.post.save(update_fields=['permissions'])
        denied = self.client_for(self.analyst).get(
            f'/api/ai/report/exports/{task_id}/download')
        self.assertEqual(denied.status_code, 403, denied.content)
        self.post.permissions = ['orders.manage']
        self.post.save(update_fields=['permissions'])
        denied = self.client_for(self.analyst).get(
            f'/api/ai/report/exports/{task_id}/download')
        self.assertEqual(denied.status_code, 403, denied.content)
        self.assertEqual(self.client_for(self.analyst).get(
            '/api/ai/report/exports').status_code, 403)

    def test_expiration_removes_private_file_and_returns_410(self):
        task_id = self.create().data['id']
        process_next_export()
        task = AiExportTask.objects.get(id=task_id)
        task.expires_at = timezone.now() - timedelta(minutes=1)
        task.save(update_fields=['expires_at'])
        self.assertEqual(expire_old_exports(), 1)
        task.refresh_from_db()
        self.assertEqual(task.status, 'expired')
        self.assertFalse(task.file_path)
        response = self.client_for(self.analyst).get(
            f'/api/ai/report/exports/{task_id}/download')
        self.assertEqual(response.status_code, 410, response.content)

    def test_invalid_and_pending_download_are_controlled(self):
        response = self.client_for(self.analyst).post('/api/ai/report/exports', {
            'plan': {**self.plan(), 'company_id': self.other_company.id}}, format='json')
        self.assertEqual(response.status_code, 400, response.content)
        task_id = self.create().data['id']
        response = self.client_for(self.analyst).get(
            f'/api/ai/report/exports/{task_id}/download')
        self.assertEqual(response.status_code, 400, response.content)
