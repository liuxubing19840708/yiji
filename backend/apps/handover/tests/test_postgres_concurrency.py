"""PostgreSQL 真实并发验证；SQLite 上不伪称覆盖行锁行为。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import Company, Post, User, UserCompany
from apps.catalog.models import Style, Ticket
from apps.handover.models import Handover, HandoverItem, HandoverKind
from apps.manufacturing.models import ProductionRoute, ProductionStage, RouteNode
from apps.manufacturing.services import complete_stage, receive_stage
from apps.production.models import Production


@skipUnless(connection.vendor == 'postgresql', '真实行锁并发仅在 PostgreSQL 上验证')
class HandoverPostgresConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.creator = User.objects.create_user(
            username='postgres-handover-creator', password='test-only')
        self.receiver = User.objects.create_user(
            username='postgres-handover-receiver', password='test-only')
        self.company = Company.objects.create(
            name='PostgreSQL并发隔离厂', company_no='920201', creator=self.creator)
        creator_post = Post.objects.create(
            company=self.company, name='发起岗', permissions=['handover.create'])
        receiver_post = Post.objects.create(
            company=self.company, name='接收岗', permissions=['handover.receive'])
        UserCompany.objects.create(
            user=self.creator, company=self.company, post=creator_post,
            status=UserCompany.Status.ACTIVE, group_no=1)
        UserCompany.objects.create(
            user=self.receiver, company=self.company, post=receiver_post,
            status=UserCompany.Status.ACTIVE, group_no=1)
        source = ProductionStage.objects.create(
            company=self.company, code='PG-HANDOVER-CUT', name='裁剪',
            stage_type=ProductionStage.StageType.CUTTING)
        target = ProductionStage.objects.create(
            company=self.company, code='PG-HANDOVER-SEW', name='缝制',
            stage_type=ProductionStage.StageType.SEWING)
        style = Style.objects.create(
            company=self.company, style_no='PG-HANDOVER', name='并发测试款')
        production = Production.objects.create(
            company=self.company, style=style, bed_no='PG-HANDOVER-BED', total_qty=50)
        route = ProductionRoute.objects.create(
            company=self.company, code='PG-HANDOVER-ROUTE', name='并发路线',
            is_template=False, production=production)
        self.source_node = RouteNode.objects.create(
            company=self.company, route=route, stage=source, sequence=1)
        self.target_node = RouteNode.objects.create(
            company=self.company, route=route, stage=target, sequence=2)
        self.ticket = Ticket.objects.create(
            company=self.company, production=production, bed_no=production.bed_no,
            bundle_no='1', color='黑', size='M', qty=50, qr_code='PG-HANDOVER-1')
        receive_stage(
            self.company, self.source_node, self.ticket, 50, operator=self.creator,
            request_id='pg-handover-fixture-receive', source='postgres_concurrency_fixture')
        complete_stage(
            self.company, self.source_node, self.ticket, 50, operator=self.creator,
            request_id='pg-handover-fixture-complete', source='postgres_concurrency_fixture')

    def test_two_concurrent_requests_cannot_over_reserve(self):
        barrier = Barrier(2)

        def create(index):
            client = APIClient()
            client.credentials(
                HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.creator)}',
                HTTP_X_COMPANY_ID=str(self.company.id))
            barrier.wait(timeout=10)
            response = client.post('/api/handovers/', {
                'kind': 'cutting', 'request_id': f'pg-handover-race-{index}',
                'source_node': self.source_node.id, 'target_node': self.target_node.id,
                'targets': [{
                    'receiver': self.receiver.id, 'target_group_no': 1,
                    'items': [{'ticket': self.ticket.id, 'quantity': 40}],
                }],
            }, format='json')
            # 让响应解析在线程内完成，避免惰性响应跨连接使用。
            return response.status_code, response.data

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(create, (1, 2)))

        self.assertEqual(sorted(status for status, _ in results), [201, 400], results)
        self.assertEqual(Handover.objects.filter(
            company=self.company, kind=HandoverKind.CUTTING,
            status=Handover.Status.PENDING).count(), 1)
        reserved = HandoverItem.objects.filter(
            company=self.company, handover__status=Handover.Status.PENDING,
            ticket=self.ticket).values_list('quantity', flat=True)
        self.assertEqual(list(reserved), [40])
