"""扫菲幂等功能测试；默认 Django test runner 使用隔离测试库。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import IntegrityError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import Company, Post, User, UserCompany
from apps.catalog.models import Process, Style, Ticket
from apps.production.models import Production
from apps.records.models import Record, ScanIdempotency


def make_fixture(prefix):
    owner = User.objects.create_user(username=f'{prefix}-owner', password='test-only')
    company = Company.objects.create(
        name=f'{prefix}-公司', company_no=str(930000 + abs(hash(prefix)) % 9000),
        creator=owner)
    post = Post.objects.create(company=company, name='管理员', is_admin=True)
    UserCompany.objects.create(company=company, user=owner, post=post,
                               status=UserCompany.Status.ACTIVE)
    process = Process.objects.create(company=company, name='车缝', default_price='2.500')
    style = Style.objects.create(company=company, style_no=f'{prefix}-S', name='测试款')
    production = Production.objects.create(
        company=company, style=style, bed_no=f'{prefix}-B', total_qty=100)
    ticket = Ticket.objects.create(
        company=company, production=production, bed_no=production.bed_no,
        bundle_no='1', qty=100, qr_code=f'{prefix}-QR')
    return company, owner, process, ticket


class ScanIdempotencyTests(TestCase):
    def setUp(self):
        self.company, self.user, self.process, self.ticket = make_fixture('idem-main')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.headers = {'HTTP_X_COMPANY_ID': str(self.company.id)}
        self.payload = {'ticket_id': self.ticket.id,
                        'process_id': self.process.id, 'qty': 4}

    def post(self, payload=None, key='offline-key-1', headers=None):
        extra = dict(self.headers)
        if key is not None:
            extra['HTTP_IDEMPOTENCY_KEY'] = key
        extra.update(headers or {})
        return self.client.post('/api/records', payload or self.payload,
                                format='json', **extra)

    def test_same_key_and_payload_replays_first_response_without_adding_qty(self):
        first = self.post()
        retry = self.post()
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(retry.status_code, 201, retry.data)
        self.assertEqual(retry.data, first.data)
        record = Record.objects.get(company=self.company)
        self.assertEqual(record.qty, 4)
        self.assertEqual(ScanIdempotency.objects.filter(company=self.company).count(), 1)

    def test_replay_is_snapshot_even_after_normal_aggregate_increment(self):
        first = self.post()
        second = self.post(key='another-operation')
        self.assertEqual(second.data['id'], first.data['id'])
        self.assertEqual(second.data['qty'], 8)
        replay = self.post()
        self.assertEqual(replay.data, first.data)
        self.assertEqual(replay.data['qty'], 4)
        self.assertEqual(Record.objects.get(pk=first.data['id']).qty, 8)

    def test_legacy_request_without_key_still_aggregates(self):
        first = self.post(key=None)
        second = self.post(key=None)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.data['id'], first.data['id'])
        self.assertEqual(Record.objects.get(pk=first.data['id']).qty, 8)
        self.assertFalse(ScanIdempotency.objects.exists())

    def test_same_key_different_payload_is_409_and_does_not_add_qty(self):
        first = self.post()
        conflict = self.post(payload={**self.payload, 'qty': 5})
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(conflict.status_code, 409, conflict.data)
        self.assertEqual(Record.objects.get(company=self.company).qty, 4)

    def test_failed_business_validation_rolls_back_receipt(self):
        rejected = self.post(payload={**self.payload, 'qty': 101})
        self.assertEqual(rejected.status_code, 400, rejected.data)
        self.assertFalse(ScanIdempotency.objects.exists())
        corrected = self.post(payload={**self.payload, 'qty': 5})
        self.assertEqual(corrected.status_code, 201, corrected.data)
        self.assertEqual(Record.objects.get(company=self.company).qty, 5)

    def test_same_key_is_isolated_by_company(self):
        other_company, _, other_process, other_ticket = make_fixture('idem-other')
        admin_post = Post.objects.get(company=other_company, is_admin=True)
        UserCompany.objects.create(company=other_company, user=self.user, post=admin_post,
                                   status=UserCompany.Status.ACTIVE)
        first = self.post()
        other = self.post(payload={'ticket_id': other_ticket.id,
                                   'process_id': other_process.id, 'qty': 7},
                          headers={'HTTP_X_COMPANY_ID': str(other_company.id)})
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(other.status_code, 201, other.data)
        self.assertNotEqual(other.data['id'], first.data['id'])
        self.assertEqual(Record.objects.get(company=other_company).qty, 7)
        self.assertEqual(ScanIdempotency.objects.filter(key='offline-key-1').count(), 2)

    def test_replay_rechecks_current_record_permission(self):
        first = self.post()
        self.assertEqual(first.status_code, 201, first.data)
        post = Post.objects.get(company=self.company, name='管理员')
        post.is_admin = False
        post.permissions = []
        post.save(update_fields=['is_admin', 'permissions', 'updated_at'])
        replay = self.post()
        self.assertEqual(replay.status_code, 403, replay.data)
        self.assertEqual(Record.objects.get(company=self.company).qty, 4)

    def test_replay_cannot_use_another_operators_key(self):
        first = self.post()
        self.assertEqual(first.status_code, 201, first.data)
        other = User.objects.create_user(username='idem-other-operator', password='test-only')
        UserCompany.objects.create(
            company=self.company, user=other,
            post=Post.objects.get(company=self.company, name='管理员'),
            status=UserCompany.Status.ACTIVE)
        self.client.force_authenticate(other)
        replay = self.post()
        self.assertEqual(replay.status_code, 403, replay.data)
        self.assertEqual(Record.objects.get(company=self.company).qty, 4)

    def test_database_unique_constraint_is_company_and_key(self):
        ScanIdempotency.objects.create(
            company=self.company, key='db-key', payload_hash='a' * 64,
            operator=self.user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScanIdempotency.objects.create(
                company=self.company, key='db-key', payload_hash='b' * 64,
                operator=self.user)


@skipUnless(connection.vendor == 'postgresql', '真实行锁并发仅在 PostgreSQL 测试库验证')
class ScanIdempotencyPostgresConcurrencyTests(TransactionTestCase):
    """同一测试库的 PostgreSQL 并发验证；SQLite 只跑上述功能与约束测试。"""

    def setUp(self):
        self.company, self.user, self.process, self.ticket = make_fixture('idem-pg-race')

    def test_concurrent_same_key_requests_create_one_increment(self):
        barrier = Barrier(2)
        token = str(AccessToken.for_user(self.user))

        def register(_):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}',
                               HTTP_X_COMPANY_ID=str(self.company.id),
                               HTTP_IDEMPOTENCY_KEY='pg-offline-race')
            try:
                barrier.wait(timeout=10)
                response = client.post('/api/records', {
                    'ticket_id': self.ticket.id, 'process_id': self.process.id, 'qty': 4,
                }, format='json')
                return response.status_code, response.data
            finally:
                # 每个线程使用独立连接；显式关闭，避免持久连接占用测试库。
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(register, (1, 2)))
        self.assertEqual([status for status, _ in results], [201, 201], results)
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(Record.objects.get(company=self.company).qty, 4)
        self.assertEqual(ScanIdempotency.objects.filter(company=self.company).count(), 1)
