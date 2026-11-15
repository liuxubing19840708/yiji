# -*- coding: utf-8 -*-
"""AI 定时报表运行中心：所有权、手动运行、重试、幂等和安全输出。"""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.ai_report.models import AiQueryExecution, AiReportSchedule, AiScheduledRun
from apps.ops.models import Notification


class ScheduleRunCenterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username='run-center-owner', phone='13900009201', password='test-only')
        cls.company, cls.owner_post = create_company_with_owner(cls.owner, '运行中心A厂')
        cls.colleague = User.objects.create_user(
            username='run-center-colleague', phone='13900009202', password='test-only')
        # 同公司所有者之外的另一名成员，用于验证运行记录不随权限或分享扩大。
        UserCompany.objects.create(
            user=cls.colleague, company=cls.company, post=cls.owner_post,
            status=UserCompany.Status.ACTIVE)
        cls.other = User.objects.create_user(
            username='run-center-other', phone='13900009203', password='test-only')
        cls.other_company, _ = create_company_with_owner(cls.other, '运行中心B厂')

    @staticmethod
    def plan(secret=None):
        filters = ([{'field': 'customer_name', 'operator': 'contains', 'value': secret}]
                   if secret else [])
        return {
            'dataset': 'orders', 'metrics': ['order_count'], 'dimensions': ['status'],
            'filters': filters, 'order_by': [], 'limit': 100,
            'include_deleted': False, 'presentation': 'table',
        }

    def client_for(self, user=None, company=None):
        client = APIClient()
        client.force_authenticate(user or self.owner)
        client.credentials(HTTP_X_COMPANY_ID=str((company or self.company).id))
        return client

    def make_schedule(self, user=None, company=None, enabled=True, secret=None):
        user = user or self.owner
        return AiReportSchedule.objects.create(
            company=company or self.company, user=user, plan=self.plan(secret),
            name='运行中心任务', frequency='daily', hour=9, minute=30,
            next_run_at=timezone.now() + timedelta(days=1), enabled=enabled)

    def post_run(self, schedule, key=None, user=None, company=None):
        return self.client_for(user, company).post(
            f'/api/ai/report/schedules/{schedule.id}/run',
            {'request_id': key or str(uuid.uuid4())}, format='json')

    def post_retry(self, run, key=None, user=None, company=None):
        return self.client_for(user, company).post(
            f'/api/ai/report/schedules/runs/{run.id}/retry',
            {'request_id': key or str(uuid.uuid4())}, format='json')

    def test_manual_run_supports_disabled_and_duplicate_request_is_idempotent(self):
        schedule = self.make_schedule(enabled=False)
        next_run_at = schedule.next_run_at
        key = str(uuid.uuid4())
        first = self.post_run(schedule, key)
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(first.data['kind'], 'manual')
        self.assertEqual(first.data['status'], 'success')
        self.assertIsNotNone(first.data['started_at'])
        self.assertIsNotNone(first.data['finished_at'])
        run = AiScheduledRun.objects.get(id=first.data['id'])
        self.assertEqual(run.attempts, 1)
        self.assertEqual(run.execution.status, 'success')
        schedule.refresh_from_db()
        self.assertFalse(schedule.enabled)
        self.assertEqual(schedule.next_run_at, next_run_at)

        repeated = self.post_run(schedule, key)
        self.assertEqual(repeated.status_code, 200, repeated.content)
        self.assertEqual(repeated.data['id'], run.id)
        self.assertEqual(AiScheduledRun.objects.filter(schedule=schedule).count(), 1)
        self.assertEqual(AiQueryExecution.objects.filter(scheduled_runs=run).count(), 1)
        self.assertEqual(Notification.objects.filter(code='ai_schedule_success').count(), 1)
        self.assertEqual(self.client_for().post(
            f'/api/ai/report/schedules/{schedule.id}/run', {}, format='json').status_code, 400)

    def test_failed_retry_creates_new_audit_and_never_rewrites_history(self):
        schedule = self.make_schedule()
        with patch('apps.ai_report.schedule_services.execute_governed_plan',
                   side_effect=RuntimeError('secret stack detail')):
            failed_response = self.post_run(schedule)
        self.assertEqual(failed_response.data['status'], 'failed')
        failed = AiScheduledRun.objects.get(id=failed_response.data['id'])
        old_finished_at = failed.finished_at
        old_execution_id = failed.execution_id

        key = str(uuid.uuid4())
        retried = self.post_retry(failed, key)
        self.assertEqual(retried.status_code, 201, retried.content)
        self.assertEqual(retried.data['kind'], 'retry')
        self.assertEqual(retried.data['retry_of_id'], failed.id)
        self.assertEqual(retried.data['status'], 'success')
        retry = AiScheduledRun.objects.get(id=retried.data['id'])
        self.assertNotEqual(retry.execution_id, old_execution_id)
        failed.refresh_from_db()
        self.assertEqual(failed.status, 'failed')
        self.assertEqual(failed.error_code, 'execution_error')
        self.assertEqual(failed.finished_at, old_finished_at)
        self.assertEqual(failed.execution_id, old_execution_id)

        repeated = self.post_retry(failed, key)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.data['id'], retry.id)
        different_key = self.post_retry(failed)
        self.assertEqual(different_key.status_code, 200)
        self.assertEqual(different_key.data['id'], retry.id)
        self.assertEqual(AiScheduledRun.objects.filter(schedule=schedule).count(), 2)
        self.assertEqual(Notification.objects.filter(code='ai_schedule_failed').count(), 1)
        self.assertEqual(Notification.objects.filter(code='ai_schedule_success').count(), 1)
        self.assertEqual(self.post_retry(retry).status_code, 400)

    def test_natural_failure_can_retry_and_only_failed_states_are_retryable(self):
        schedule = self.make_schedule()
        source = AiScheduledRun.objects.create(
            schedule=schedule, scheduled_at=timezone.now(), kind='natural',
            status='denied', attempts=1, error_code='permission_denied',
            started_at=timezone.now(), finished_at=timezone.now())
        response = self.post_retry(source)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data['kind'], 'retry')
        self.assertEqual(AiScheduledRun.objects.get(id=source.id).status, 'denied')
        success = AiScheduledRun.objects.get(id=response.data['id'])
        self.assertEqual(self.post_retry(success).status_code, 400)

    def test_run_list_is_owner_only_and_has_no_plan_results_or_exception_text(self):
        secret = '运行中心绝密筛选值'
        own = self.make_schedule(secret=secret)
        other_user_schedule = self.make_schedule(user=self.colleague)
        other_company_schedule = self.make_schedule(user=self.other, company=self.other_company)
        with patch('apps.ai_report.schedule_services.execute_governed_plan',
                   side_effect=RuntimeError(secret)):
            failed = self.post_run(own)
        self.post_run(other_user_schedule, user=self.colleague)
        self.post_run(other_company_schedule, user=self.other, company=self.other_company)

        response = self.client_for().get('/api/ai/report/schedules/runs')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([item['id'] for item in response.data['results']], [failed.data['id']])
        self.assertEqual(set(response.data['results'][0]), {
            'id', 'schedule_id', 'schedule_name', 'kind', 'scheduled_at', 'started_at',
            'finished_at', 'status', 'outcome', 'error_code', 'error_summary', 'retry_of_id',
        })
        serialized = response.content.decode()
        self.assertNotIn(secret, serialized)
        self.assertNotIn('secret stack', serialized)
        self.assertNotIn('plan', serialized)
        self.assertNotIn('row_count', serialized)
        self.assertEqual(response.data['results'][0]['error_summary'], '执行失败，请稍后重试')
        filtered = self.client_for().get(
            f'/api/ai/report/schedules/runs?schedule_id={other_user_schedule.id}')
        self.assertEqual(filtered.data['results'], [])

    def test_cross_user_company_and_permission_revocation_are_enforced(self):
        own = self.make_schedule()
        response = self.post_run(own)
        run = AiScheduledRun.objects.get(id=response.data['id'])
        for user, company in ((self.colleague, self.company),
                              (self.other, self.other_company)):
            self.assertEqual(self.post_run(own, user=user, company=company).status_code, 404)
            self.assertEqual(self.post_retry(run, user=user, company=company).status_code, 404)

        self.owner_post.permissions = ['orders.manage']
        self.owner_post.is_admin = False
        self.owner_post.save(update_fields=['permissions', 'is_admin'])
        before = AiScheduledRun.objects.count()
        self.assertEqual(self.post_run(own).status_code, 403)
        self.assertEqual(self.post_retry(run).status_code, 403)
        self.assertEqual(self.client_for().get('/api/ai/report/schedules/runs').status_code, 403)
        self.assertEqual(AiScheduledRun.objects.count(), before)

    def test_manual_scheduled_at_does_not_collide_with_natural_run(self):
        schedule = self.make_schedule()
        moment = timezone.now()
        natural = AiScheduledRun.objects.create(
            schedule=schedule, scheduled_at=moment, kind='natural', status='failed',
            error_code='execution_error', started_at=moment, finished_at=moment)
        with patch('apps.ai_report.schedule_services.timezone.now', return_value=moment):
            response = self.post_run(schedule)
        self.assertEqual(response.status_code, 201, response.content)
        manual = AiScheduledRun.objects.get(id=response.data['id'])
        self.assertEqual(manual.scheduled_at, moment + timedelta(microseconds=1))
        self.assertEqual(AiScheduledRun.objects.get(id=natural.id).status, 'failed')
