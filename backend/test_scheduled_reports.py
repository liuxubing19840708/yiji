# -*- coding: utf-8 -*-
"""定时 AI 报表：调度推进、实时权限、通知边界、防重与显式 worker。"""
from datetime import datetime, timedelta, timezone as dt_timezone
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Post, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.ai_report.models import (
    AiQueryExecution, AiReportSchedule, AiScheduledRun, SavedReport,
)
from apps.ai_report.schedule_services import next_run_after, process_next_schedule
from apps.ops.models import Notification


class ScheduledReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username='schedule-owner', phone='13900009101', password='test-only')
        cls.company, cls.owner_post = create_company_with_owner(cls.owner, '定时报表A厂')
        cls.owner_link = UserCompany.objects.get(user=cls.owner, company=cls.company)
        cls.analyst_post = Post.objects.create(
            company=cls.company, name='定时分析',
            permissions=['orders.manage', 'ai.query', 'ai.share'])
        cls.analyst = User.objects.create_user(
            username='schedule-analyst', phone='13900009102', password='test-only')
        cls.analyst_link = UserCompany.objects.create(
            user=cls.analyst, company=cls.company, post=cls.analyst_post,
            status=UserCompany.Status.ACTIVE)
        cls.plain_post = Post.objects.create(
            company=cls.company, name='定时普工', permissions=['orders.manage'])
        cls.plain = User.objects.create_user(
            username='schedule-plain', phone='13900009103', password='test-only')
        UserCompany.objects.create(
            user=cls.plain, company=cls.company, post=cls.plain_post,
            status=UserCompany.Status.ACTIVE)
        cls.other = User.objects.create_user(
            username='schedule-other', phone='13900009104', password='test-only')
        cls.other_company, _ = create_company_with_owner(cls.other, '定时报表B厂')

    @staticmethod
    def plan(secret=None):
        filters = []
        if secret:
            filters = [{'field': 'customer_name', 'operator': 'contains', 'value': secret}]
        return {
            'dataset': 'orders', 'metrics': ['order_count'], 'dimensions': ['status'],
            'filters': filters, 'order_by': [], 'limit': 100,
            'include_deleted': False, 'presentation': 'table',
        }

    def client_for(self, user, company=None):
        client = APIClient()
        client.force_authenticate(user)
        client.credentials(HTTP_X_COMPANY_ID=str((company or self.company).id))
        return client

    def create_api(self, user=None, company=None, expected=201, **data):
        payload = {
            'name': '每日订单定时报表', 'plan': self.plan(),
            'frequency': 'daily', 'hour': 9, 'minute': 30,
        }
        payload.update(data)
        response = self.client_for(user or self.owner, company).post(
            '/api/ai/report/schedules', payload, format='json')
        self.assertEqual(response.status_code, expected, response.content)
        return response

    def make_due(self, user=None, report=None, secret=None, name='到期任务'):
        schedule = AiReportSchedule.objects.create(
            company=(user or self.owner).company_links.filter(
                company=self.company).get().company,
            user=user or self.owner, saved_report=report,
            plan=None if report else self.plan(secret), name=name,
            frequency='daily', hour=9, minute=30,
            next_run_at=timezone.now() - timedelta(minutes=1), enabled=True)
        return schedule

    def test_next_time_is_strict_daily_weekly_and_monthly_without_month_rollover(self):
        after = datetime(2026, 1, 30, 0, 0, tzinfo=dt_timezone.utc)
        daily = SimpleNamespace(
            frequency='daily', hour=8, minute=0, weekday=None,
            day_of_month=None, timezone_name='Asia/Shanghai')
        value = next_run_after(daily, after)
        self.assertEqual(timezone.localtime(value).strftime('%Y-%m-%d %H:%M'), '2026-01-31 08:00')
        self.assertGreater(next_run_after(daily, value), value)

        weekly = SimpleNamespace(
            frequency='weekly', hour=9, minute=15, weekday=0,
            day_of_month=None, timezone_name='Asia/Shanghai')
        monday = next_run_after(weekly, after)
        self.assertEqual(timezone.localtime(monday).weekday(), 0)
        self.assertEqual(timezone.localtime(monday).strftime('%H:%M'), '09:15')
        self.assertEqual(
            (next_run_after(weekly, monday) - monday).total_seconds(), 7 * 86400)

        monthly = SimpleNamespace(
            frequency='monthly', hour=9, minute=0, weekday=None,
            day_of_month=31, timezone_name='Asia/Shanghai')
        january = next_run_after(monthly, after)
        self.assertEqual(timezone.localtime(january).strftime('%Y-%m-%d %H:%M'), '2026-01-31 09:00')
        march = next_run_after(monthly, january)
        self.assertEqual(timezone.localtime(march).strftime('%Y-%m-%d %H:%M'), '2026-03-31 09:00')

    def test_owner_crud_private_permission_share_permission_and_isolation(self):
        created = self.create_api(user=self.analyst)
        schedule_id = created.data['id']
        self.assertEqual(created.data['plan']['metrics'][0]['key'], 'order_count')
        listed = self.client_for(self.analyst).get('/api/ai/report/schedules')
        self.assertEqual([item['id'] for item in listed.data['results']], [schedule_id])
        patched = self.client_for(self.analyst).patch(
            f'/api/ai/report/schedules/{schedule_id}',
            {'frequency': 'weekly', 'weekday': 2, 'hour': 10}, format='json')
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual(patched.data['weekday'], 2)
        self.assertIsNone(patched.data['day_of_month'])

        for user, company in ((self.plain, self.company), (self.other, self.other_company)):
            client = self.client_for(user, company)
            self.assertEqual(client.get(
                f'/api/ai/report/schedules/{schedule_id}').status_code, 404)
            self.assertEqual(client.delete(
                f'/api/ai/report/schedules/{schedule_id}').status_code, 404)
            listed_other = client.get('/api/ai/report/schedules')
            if user == self.other:
                self.assertEqual(listed_other.data['results'], [])
            else:
                self.assertEqual(listed_other.status_code, 403)

        self.analyst_post.permissions = ['orders.manage']
        self.analyst_post.save(update_fields=['permissions'])
        self.assertEqual(self.client_for(self.analyst).get(
            '/api/ai/report/schedules').status_code, 403)
        self.create_api(user=self.analyst, expected=403)

    def test_shared_saved_report_schedule_requires_ai_share(self):
        report = SavedReport.objects.create(
            company=self.company, user=self.analyst, name='公司共享报表',
            dataset='orders', plan=self.plan(), visibility='company')
        self.analyst_post.permissions = ['orders.manage', 'ai.query']
        self.analyst_post.save(update_fields=['permissions'])
        self.create_api(user=self.analyst, plan=None, saved_report_id=report.id, expected=403)
        self.analyst_post.permissions = ['orders.manage', 'ai.query', 'ai.share']
        self.analyst_post.save(update_fields=['permissions'])
        created = self.create_api(user=self.analyst, plan=None, saved_report_id=report.id)
        self.assertEqual(created.data['saved_report_id'], report.id)
        self.assertIsNone(created.data['plan'])

    def test_success_notification_has_saved_link_but_no_filter_or_result(self):
        secret = '绝密客户筛选值'
        report = SavedReport.objects.create(
            company=self.company, user=self.owner, name='机密报表', dataset='orders',
            plan=self.plan(secret), visibility='private')
        schedule = self.make_due(report=report, secret=secret)
        run_id = process_next_schedule()
        run = AiScheduledRun.objects.get(id=run_id)
        self.assertEqual(run.status, 'success')
        self.assertEqual(run.execution.status, 'success')
        self.assertEqual(run.attempts, 1)
        notifications = Notification.objects.filter(code='ai_schedule_success')
        self.assertEqual(notifications.count(), 1)
        message = notifications.get().message
        self.assertIn(f'ID：{report.id}', message)
        self.assertIn(f'/ai/report/saved/{report.id}', message)
        self.assertNotIn(secret, message)
        self.assertNotIn('row_count', message)
        self.assertGreater(schedule.__class__.objects.get(id=schedule.id).next_run_at,
                           run.scheduled_at)
        self.assertIsNone(process_next_schedule())

    def test_company_share_recipients_are_rechecked_on_every_run(self):
        report = SavedReport.objects.create(
            company=self.company, user=self.owner, name='公司报表', dataset='orders',
            plan=self.plan(), visibility='company')
        schedule = self.make_due(report=report)
        process_next_schedule()
        self.assertEqual(Notification.objects.filter(
            code='ai_schedule_success', user=self.owner).count(), 1)
        self.assertEqual(Notification.objects.filter(
            code='ai_schedule_success', user=self.analyst).count(), 1)
        self.assertFalse(Notification.objects.filter(
            code='ai_schedule_success', user=self.plain).exists())
        self.assertFalse(Notification.objects.filter(
            code='ai_schedule_success', company=self.other_company).exists())

        self.analyst_post.permissions = []
        self.analyst_post.save(update_fields=['permissions'])
        schedule.next_run_at = timezone.now() - timedelta(minutes=1)
        schedule.save(update_fields=['next_run_at', 'updated_at'])
        process_next_schedule()
        self.assertEqual(AiScheduledRun.objects.filter(schedule=schedule).count(), 2)
        self.assertEqual(Notification.objects.filter(
            code='ai_schedule_success', user=self.owner).count(), 2)
        self.assertEqual(Notification.objects.filter(
            code='ai_schedule_success', user=self.analyst).count(), 1)

    def test_selected_acl_controls_recipients_and_acl_removal_stops_notification(self):
        report = SavedReport.objects.create(
            company=self.company, user=self.owner, name='指定成员报表', dataset='orders',
            plan=self.plan(), visibility='selected')
        share = report.shares.create(user=self.analyst)
        schedule = self.make_due(report=report)
        process_next_schedule()
        self.assertTrue(Notification.objects.filter(
            code='ai_schedule_success', user=self.analyst).exists())
        self.assertFalse(Notification.objects.filter(
            code='ai_schedule_success', user=self.plain).exists())

        share.delete()
        schedule.next_run_at = timezone.now() - timedelta(minutes=1)
        schedule.save(update_fields=['next_run_at', 'updated_at'])
        process_next_schedule()
        self.assertEqual(Notification.objects.filter(
            code='ai_schedule_success', user=self.analyst).count(), 1)
        self.assertEqual(Notification.objects.filter(
            code='ai_schedule_success', user=self.owner).count(), 2)

    def test_owner_permission_revocation_is_denied_and_not_retried_every_poll(self):
        schedule = self.make_due(user=self.analyst)
        self.analyst_post.permissions = ['orders.manage', 'ai.share']
        self.analyst_post.save(update_fields=['permissions'])
        run_id = process_next_schedule()
        run = AiScheduledRun.objects.get(id=run_id)
        self.assertEqual(run.status, 'denied')
        self.assertEqual(run.error_code, 'permission_denied')
        self.assertEqual(run.execution.status, 'denied')
        schedule.refresh_from_db()
        self.assertGreater(schedule.next_run_at, run.scheduled_at)
        self.assertIsNone(process_next_schedule())
        failures = Notification.objects.filter(code='ai_schedule_failed')
        self.assertEqual(failures.count(), 1)
        self.assertEqual(failures.get().user, self.analyst)

    def test_invalid_and_failed_runs_persist_audit_advance_and_do_not_leak(self):
        invalid = self.make_due(user=self.analyst, name='无效计划')
        invalid.plan = {**self.plan(), 'metrics': []}
        invalid.save(update_fields=['plan'])
        invalid_id = process_next_schedule()
        invalid_run = AiScheduledRun.objects.get(id=invalid_id)
        self.assertEqual(invalid_run.status, 'invalid')
        self.assertEqual(invalid_run.error_code, 'invalid_plan')

        secret = '异常中也不能出现的筛选值'
        failed = self.make_due(user=self.analyst, secret=secret, name='异常计划')
        failed.next_run_at = timezone.now() - timedelta(minutes=1)
        failed.save(update_fields=['next_run_at'])
        with patch('apps.ai_report.schedule_services.execute_governed_plan',
                   side_effect=RuntimeError(secret)):
            failed_id = process_next_schedule()
        failed_run = AiScheduledRun.objects.get(id=failed_id)
        self.assertEqual(failed_run.status, 'failed')
        self.assertEqual(failed_run.error_code, 'execution_error')
        self.assertEqual(failed_run.attempts, 1)
        failed.refresh_from_db()
        self.assertGreater(failed.next_run_at, failed_run.scheduled_at)
        messages = ' '.join(Notification.objects.filter(
            code='ai_schedule_failed', user=self.analyst).values_list('message', flat=True))
        self.assertNotIn(secret, messages)
        self.assertNotIn(str(failed.plan), messages)
        self.assertIsNone(process_next_schedule())

    def test_unique_scheduled_time_prevents_duplicate_run(self):
        schedule = self.make_due()
        run_id = process_next_schedule()
        run = AiScheduledRun.objects.get(id=run_id)
        schedule.next_run_at = run.scheduled_at
        schedule.save(update_fields=['next_run_at'])
        self.assertIsNone(process_next_schedule(now=run.scheduled_at))
        self.assertEqual(AiScheduledRun.objects.filter(schedule=schedule).count(), 1)
        self.assertEqual(Notification.objects.filter(code='ai_schedule_success').count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AiScheduledRun.objects.create(schedule=schedule, scheduled_at=run.scheduled_at)

    def test_management_command_once_is_explicit_and_does_not_auto_run(self):
        schedule = self.make_due()
        self.assertFalse(AiScheduledRun.objects.filter(schedule=schedule).exists())
        self.assertFalse(Notification.objects.filter(code='ai_schedule_success').exists())
        output = StringIO()
        call_command('process_ai_schedules', '--once', stdout=output)
        self.assertIn('processed=', output.getvalue())
        run = AiScheduledRun.objects.get(schedule=schedule)
        self.assertEqual(run.status, 'success')
        self.assertEqual(Notification.objects.filter(code='ai_schedule_success').count(), 1)
        output = StringIO()
        call_command('process_ai_schedules', '--once', stdout=output)
        self.assertIn('idle', output.getvalue())
