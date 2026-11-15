# -*- coding: utf-8 -*-
"""真实本机 HTTP 交接流程；LiveServerTestCase 自动创建并销毁隔离测试库。"""
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.test import LiveServerTestCase
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import Company, Post, User, UserCompany
from apps.catalog.models import Style, Ticket
from apps.handover.models import Handover
from apps.manufacturing.models import ProductionRoute, ProductionStage, QuantityLedger, RouteNode
from apps.manufacturing.services import complete_stage, receive_stage
from apps.production.models import Production


class HandoverLiveHttpTests(LiveServerTestCase):
    def setUp(self):
        self.creator = User.objects.create_user(
            username='live-handover-creator', password='test-only', real_name='真实请求发起人')
        self.receiver = User.objects.create_user(
            username='live-handover-receiver', password='test-only', real_name='真实请求接收人')
        self.company = Company.objects.create(
            name='真实HTTP隔离交接厂', company_no='920101', creator=self.creator)
        creator_post = Post.objects.create(
            company=self.company, name='发起岗',
            permissions=['handover.view', 'handover.create'])
        receiver_post = Post.objects.create(
            company=self.company, name='接收岗',
            permissions=['handover.view', 'handover.receive'])
        UserCompany.objects.create(
            user=self.creator, company=self.company, post=creator_post,
            status=UserCompany.Status.ACTIVE, group_no=1)
        UserCompany.objects.create(
            user=self.receiver, company=self.company, post=receiver_post,
            status=UserCompany.Status.ACTIVE, group_no=1)
        source = ProductionStage.objects.create(
            company=self.company, code='LIVE-HANDOVER-CUT', name='裁剪',
            stage_type=ProductionStage.StageType.CUTTING)
        target = ProductionStage.objects.create(
            company=self.company, code='LIVE-HANDOVER-SEW', name='缝制',
            stage_type=ProductionStage.StageType.SEWING)
        route = ProductionRoute.objects.create(
            company=self.company, code='LIVE-HANDOVER-ROUTE', name='真实HTTP路线',
            is_template=True)
        self.source_node = RouteNode.objects.create(
            company=self.company, route=route, stage=source, sequence=1)
        self.target_node = RouteNode.objects.create(
            company=self.company, route=route, stage=target, sequence=2)
        style = Style.objects.create(
            company=self.company, style_no='LIVE-HANDOVER', name='真实HTTP测试款')
        production = Production.objects.create(
            company=self.company, style=style, bed_no='LIVE-HANDOVER-BED', total_qty=50)
        self.ticket = Ticket.objects.create(
            company=self.company, production=production, bed_no=production.bed_no,
            bundle_no='1', color='黑', size='M', qty=50, qr_code='LIVE-HANDOVER-1')
        route.is_template = False
        route.production = production
        route.save(update_fields=['is_template', 'production', 'updated_at'])
        receive_stage(self.company, self.source_node, self.ticket, 50,
                      operator=self.creator, request_id='live-handover-fixture-receive',
                      source='live_http_fixture')
        complete_stage(self.company, self.source_node, self.ticket, 50,
                       operator=self.creator, request_id='live-handover-fixture-complete',
                       source='live_http_fixture')

    def request(self, method, path, user, payload=None):
        body = None if payload is None else json.dumps(payload).encode('utf-8')
        request = Request(
            f'{self.live_server_url}{path}', data=body, method=method,
            headers={
                'Authorization': f'Bearer {AccessToken.for_user(user)}',
                'X-Company-Id': str(self.company.id),
                'Content-Type': 'application/json',
            })
        try:
            with urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            self.fail(f'{method} {path} 返回 {exc.code}: {exc.read().decode("utf-8")}')

    def test_create_and_difference_receive_over_real_http(self):
        status, created = self.request('POST', '/api/handovers/', self.creator, {
            'kind': 'cutting', 'request_id': 'live-handover-create',
            'source_node': self.source_node.id, 'target_node': self.target_node.id,
            'remark': '真实本机HTTP隔离验证',
            'targets': [{
                'receiver': self.receiver.id, 'target_group_no': 1,
                'items': [{'ticket': self.ticket.id, 'quantity': 50}],
            }],
        })
        self.assertEqual(status, 201, created)
        handover_id = created['items'][0]['id']
        item_id = created['items'][0]['items'][0]['id']
        status, received = self.request(
            'POST', f'/api/handovers/{handover_id}/receive', self.receiver, {
                'request_id': 'live-handover-receive', 'reason': '短少5件留裁床核查',
                'items': [{
                    'id': item_id, 'received_quantity': 45,
                    'difference_type': 'shortage', 'reason': '实物仅45件',
                }],
            })
        self.assertEqual(status, 200, received)
        self.assertEqual(received['status'], 'received')
        self.assertEqual(received['received_quantity'], 45)
        handover = Handover.objects.get(pk=handover_id)
        item = handover.items.get()
        self.assertEqual(item.received_quantity, 45)
        self.assertIsNotNone(item.out_ledger)
        self.assertIsNotNone(item.in_ledger)
        outgoing = QuantityLedger.objects.get(pk=item.out_ledger_id)
        incoming = QuantityLedger.objects.get(pk=item.in_ledger_id)
        self.assertEqual((outgoing.quantity, outgoing.node_id), (45, self.source_node.id))
        self.assertEqual((incoming.quantity, incoming.node_id), (45, self.target_node.id))
