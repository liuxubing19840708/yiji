# -*- coding: utf-8 -*-
"""第一期通用交接 API 冻结契约测试。

只使用 Django 隔离测试库和自建公司/成员/节点/菲票，不访问开发库，也不依赖
GLM 或真实业务数据；可在 SQLite/PostgreSQL 上运行，裁片数量事实由夹具在隔离库内自行建立。
路由未注册时应明确失败，
不得通过跳过用例或放宽业务断言来迁就尚未完成的实现。
"""
from copy import deepcopy
from uuid import uuid4

from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import Company, Post, User, UserCompany
from apps.catalog.models import Style, Ticket
from apps.manufacturing.models import ProductionRoute, ProductionStage, RouteNode
from apps.manufacturing.services import complete_stage, receive_stage
from apps.production.models import Production


def rid():
    return uuid4().hex


class HandoverContractTests(TestCase):
    """/api/handovers 的独立端到端契约，不调用被测服务或导入其实现模型。"""

    @classmethod
    def setUpTestData(cls):
        def user(username, real_name):
            return User.objects.create_user(
                username=f'handover-{username}', password='contract-test-only',
                real_name=real_name, is_active=True)

        cls.creator = user('creator', '交接发起人')
        cls.manager = user('manager', '交接管理员')
        cls.receiver = user('receiver', '一组接收人')
        cls.pool_receiver = user('pool-receiver', '二组接收人')
        cls.other_group_receiver = user('other-group-receiver', '三组接收人')
        cls.new_receiver = user('new-receiver', '改派接收人')
        cls.plain = user('plain', '仅查看成员')
        cls.suspended = user('suspended', '停职接收人')
        cls.other_admin = user('other-admin', '二厂管理员')

        cls.company = Company.objects.create(
            name='交接契约一厂', company_no='920001', creator=cls.creator)
        cls.other_company = Company.objects.create(
            name='交接契约二厂', company_no='920002', creator=cls.other_admin)

        def post(company, name, permissions):
            return Post.objects.create(company=company, name=name, permissions=permissions)

        creator_post = post(
            cls.company, '交接发起岗', ['handover.view', 'handover.create'])
        manager_post = post(
            cls.company, '交接管理岗',
            ['handover.view', 'handover.create', 'handover.manage'])
        receive_post = post(
            cls.company, '交接接收岗', ['handover.view', 'handover.receive'])
        view_post = post(cls.company, '交接查看岗', ['handover.view'])
        other_post = post(
            cls.other_company, '二厂交接管理岗',
            ['handover.view', 'handover.create', 'handover.receive', 'handover.manage'])

        def member(user_obj, company, post_obj, group=1, **kwargs):
            return UserCompany.objects.create(
                user=user_obj, company=company, post=post_obj,
                status=UserCompany.Status.ACTIVE, group_no=group, **kwargs)

        member(cls.creator, cls.company, creator_post)
        member(cls.manager, cls.company, manager_post)
        member(cls.receiver, cls.company, receive_post, group=1)
        member(cls.pool_receiver, cls.company, receive_post, group=2)
        member(cls.other_group_receiver, cls.company, receive_post, group=3)
        member(cls.new_receiver, cls.company, receive_post, group=3)
        member(cls.plain, cls.company, view_post, group=1)
        member(cls.suspended, cls.company, receive_post, group=1, is_suspended=True)
        member(cls.other_admin, cls.other_company, other_post, group=1)

        cls.source_node, cls.target_node = cls.make_nodes(cls.company, 'A')
        cls.other_source_node, cls.other_target_node = cls.make_nodes(
            cls.other_company, 'B')
        cls.tickets = cls.make_tickets(cls.company, 'A', [50, 60, 30, 20])
        cls.other_tickets = cls.make_tickets(cls.other_company, 'B', [40])
        for node, ticket in ((cls.source_node, cls.tickets[0]),
                             (cls.other_source_node, cls.other_tickets[0])):
            node.route.is_template = False
            node.route.production = ticket.production
            node.route.save(update_fields=['is_template', 'production', 'updated_at'])
        # 裁片适配后创建交接会占用真实可转量；测试夹具显式建立首节点接收与合格事实。
        for prefix, company, node, tickets in (
                ('A', cls.company, cls.source_node, cls.tickets),
                ('B', cls.other_company, cls.other_source_node, cls.other_tickets)):
            for index, ticket in enumerate(tickets, start=1):
                receive_stage(company, node, ticket, ticket.qty,
                              operator=cls.manager if prefix == 'A' else cls.other_admin,
                              request_id=f'contract-{prefix}-{index}-receive',
                              source='handover_contract_fixture')
                complete_stage(company, node, ticket, ticket.qty,
                               operator=cls.manager if prefix == 'A' else cls.other_admin,
                               request_id=f'contract-{prefix}-{index}-complete',
                               source='handover_contract_fixture')

    @classmethod
    def make_nodes(cls, company, prefix):
        source = ProductionStage.objects.create(
            company=company, code=f'HANDOVER-CUT-{prefix}', name='裁剪',
            stage_type=ProductionStage.StageType.CUTTING)
        target = ProductionStage.objects.create(
            company=company, code=f'HANDOVER-SEW-{prefix}', name='缝制',
            stage_type=ProductionStage.StageType.SEWING)
        route = ProductionRoute.objects.create(
            company=company, code=f'HANDOVER-ROUTE-{prefix}', name='交接契约路线',
            is_template=True)
        source_node = RouteNode.objects.create(
            company=company, route=route, stage=source, sequence=1)
        target_node = RouteNode.objects.create(
            company=company, route=route, stage=target, sequence=2)
        return source_node, target_node

    @classmethod
    def make_tickets(cls, company, prefix, quantities):
        style = Style.objects.create(
            company=company, style_no=f'HANDOVER-{prefix}', name='交接契约测试款')
        production = Production.objects.create(
            company=company, style=style, bed_no=f'HANDOVER-BED-{prefix}',
            total_qty=sum(quantities))
        return [
            Ticket.objects.create(
                company=company, production=production, bed_no=production.bed_no,
                bundle_no=str(index), color='黑', size='M', qty=quantity,
                qr_code=f'HANDOVER-{prefix}-{index}')
            for index, quantity in enumerate(quantities, start=1)
        ]

    def api_client(self, user, company=None):
        company = company or self.company
        client = APIClient()
        client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}',
            HTTP_X_COMPANY_ID=str(company.id))
        return client

    def target(self, ticket, quantity, *, receiver=None, group=1):
        return {
            'receiver': receiver.id if receiver is not None else None,
            'target_group_no': group,
            'items': [{'ticket': ticket.id, 'quantity': quantity}],
        }

    def create_payload(self, targets=None, *, request_id=None, remark=''):
        return {
            'kind': 'cutting',
            'request_id': request_id or rid(),
            'source_node': self.source_node.id,
            'target_node': self.target_node.id,
            'remark': remark,
            'targets': targets if targets is not None else [
                self.target(self.tickets[0], 50, receiver=self.receiver)],
        }

    def create_handover_batch(self, targets=None, *, request_id=None, remark='',
                              user=None, company=None, payload=None):
        user = user or self.creator
        company = company or self.company
        body = payload if payload is not None else self.create_payload(
            targets, request_id=request_id, remark=remark)
        response = self.api_client(user, company).post(
            '/api/handovers/', body, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsInstance(response.data.get('items'), list, response.data)
        self.assertTrue(response.data.get('batch_no'), response.data)
        return response.data

    def detail(self, handover_id, *, user=None, company=None):
        return self.api_client(user or self.manager, company or self.company).get(
            f'/api/handovers/{handover_id}/')

    def policy_rows(self, user, company):
        response = self.api_client(user, company).get('/api/handovers/policies')
        self.assertEqual(response.status_code, 200, response.data)
        if isinstance(response.data, list):
            return response.data
        self.assertIsInstance(response.data.get('results'), list, response.data)
        return response.data['results']

    def assert_handover_shape(self, handover):
        required = {
            'id', 'handover_no', 'batch_no', 'kind', 'status',
            'source_node', 'source_node_name', 'target_node', 'target_node_name',
            'target_group_no', 'receiver', 'receiver_name', 'created_by',
            'created_by_name', 'received_by', 'received_by_name', 'received_at',
            'reject_reason', 'has_discrepancy', 'total_quantity',
            'received_quantity', 'remark', 'created_at', 'items', 'events',
        }
        self.assertFalse(required.difference(handover), handover)
        item_required = {
            'id', 'ticket', 'ticket_label', 'quantity', 'received_quantity',
            'difference_type', 'reason',
        }
        for item in handover['items']:
            self.assertFalse(item_required.difference(item), item)
        event_required = {
            'id', 'action', 'actor', 'actor_name', 'actual_user',
            'actual_user_name', 'reason', 'before', 'after', 'created_at',
        }
        for event in handover['events']:
            self.assertFalse(event_required.difference(event), event)

    def test_default_policy_and_policy_company_isolation(self):
        default_rows = self.policy_rows(self.manager, self.company)
        cutting = [row for row in default_rows if row.get('kind') == 'cutting']
        self.assertEqual(len(cutting), 1, default_rows)
        self.assertEqual(cutting[0]['mode'], 'off', cutting[0])

        payload = {
            'kind': 'cutting', 'mode': 'warn',
            'source_node': self.source_node.id, 'target_node': self.target_node.id,
            'target_group_no': 1, 'receiver': self.receiver.id,
            'timeout_minutes': 120,
        }
        denied = self.api_client(self.creator, self.company).post(
            '/api/handovers/policies', payload, format='json')
        self.assertEqual(denied.status_code, 403, denied.data)
        created = self.api_client(self.manager, self.company).post(
            '/api/handovers/policies', payload, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['mode'], 'warn')
        self.assertEqual(created.data['updated_by_name'], '交接管理员')

        rows = self.policy_rows(self.manager, self.company)
        cutting_rows = [row for row in rows if row['kind'] == 'cutting']
        self.assertEqual(len(cutting_rows), 2, cutting_rows)
        self.assertIn(created.data['id'], [row['id'] for row in cutting_rows])
        default = next(row for row in cutting_rows
                       if row['source_node'] is None and row['target_node'] is None)
        self.assertEqual(default['mode'], 'off', default)
        self.assertIsNone(default['id'], default)
        other_rows = self.policy_rows(self.other_admin, self.other_company)
        other_cutting = [row for row in other_rows if row.get('kind') == 'cutting']
        self.assertEqual(len(other_cutting), 1, other_rows)
        self.assertEqual(other_cutting[0]['mode'], 'off', other_cutting[0])
        self.assertNotEqual(other_cutting[0].get('id'), created.data['id'])

        foreign_update = self.api_client(self.other_admin, self.other_company).put(
            f"/api/handovers/policies/{created.data['id']}", payload, format='json')
        self.assertEqual(foreign_update.status_code, 404, foreign_update.data)
        current_rows = self.policy_rows(self.manager, self.company)
        unchanged = next(row for row in current_rows if row['id'] == created.data['id'])
        self.assertEqual(unchanged['mode'], 'warn')

    def test_batch_create_is_one_to_many_and_keeps_responsibility(self):
        batch = self.create_handover_batch([
            self.target(self.tickets[0], 50, receiver=self.receiver, group=1),
            self.target(self.tickets[1], 60, receiver=None, group=2),
        ], remark='一对多契约')
        self.assertEqual(len(batch['items']), 2)
        self.assertEqual({row['batch_no'] for row in batch['items']}, {batch['batch_no']})
        self.assertEqual(len({row['handover_no'] for row in batch['items']}), 2)
        self.assertTrue(all(row['handover_no'] for row in batch['items']))

        designated = next(row for row in batch['items'] if row['receiver'] is not None)
        pool = next(row for row in batch['items'] if row['receiver'] is None)
        self.assertEqual(designated['receiver'], self.receiver.id)
        self.assertEqual(designated['receiver_name'], '一组接收人')
        self.assertEqual(designated['target_group_no'], 1)
        self.assertEqual(pool['target_group_no'], 2)
        self.assertEqual([row['total_quantity'] for row in batch['items']], [50, 60])
        for handover in batch['items']:
            self.assert_handover_shape(handover)
            self.assertEqual(handover['status'], 'pending')
            self.assertEqual(handover['created_by'], self.creator.id)
            self.assertEqual(handover['created_by_name'], '交接发起人')
            self.assertIsNone(handover['received_by'])
            self.assertIsNone(handover['received_at'])
            self.assertEqual([event['action'] for event in handover['events']], ['created'])
            self.assertEqual(handover['events'][0]['actor'], self.creator.id)

        listed = self.api_client(self.creator, self.company).get(
            '/api/handovers/', {'box': 'created', 'batch_no': batch['batch_no'],
                                'kind': 'cutting'})
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data['count'], 2)
        self.assertEqual({row['id'] for row in listed.data['results']},
                         {row['id'] for row in batch['items']})

    def test_same_ticket_cannot_be_allocated_twice_and_create_is_atomic(self):
        payload = self.create_payload([
            self.target(self.tickets[0], 20, receiver=self.receiver, group=1),
            self.target(self.tickets[0], 30, receiver=None, group=2),
        ])
        response = self.api_client(self.creator, self.company).post(
            '/api/handovers/', payload, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        listed = self.api_client(self.creator, self.company).get(
            '/api/handovers/', {'box': 'created', 'kind': 'cutting'})
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data['count'], 0, listed.data)

    def test_unspecified_receiver_is_received_by_active_target_group_pool_member(self):
        handover = self.create_handover_batch([
            self.target(self.tickets[1], 60, receiver=None, group=2)])['items'][0]
        response = self.api_client(self.pool_receiver, self.company).post(
            f"/api/handovers/{handover['id']}/receive", {'request_id': rid()}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'received')
        self.assertIsNone(response.data['receiver'])
        self.assertEqual(response.data['received_by'], self.pool_receiver.id)
        self.assertEqual(response.data['received_by_name'], '二组接收人')
        self.assertIsNotNone(response.data['received_at'])
        self.assertEqual(response.data['received_quantity'], 60)
        self.assertEqual(response.data['items'][0]['received_quantity'], 60)
        self.assertFalse(response.data['has_discrepancy'])
        self.assertEqual([event['action'] for event in response.data['events']],
                         ['created', 'received'])
        received_event = response.data['events'][-1]
        self.assertEqual(received_event['actor'], self.pool_receiver.id)
        self.assertEqual(received_event['actual_user'], self.pool_receiver.id)

    def test_designated_receiver_requires_self_and_receive_permission(self):
        handover = self.create_handover_batch([
            self.target(self.tickets[0], 50, receiver=self.receiver, group=1)])['items'][0]
        path = f"/api/handovers/{handover['id']}/receive"
        for user in (self.plain, self.pool_receiver, self.manager):
            with self.subTest(user=user.username):
                denied = self.api_client(user, self.company).post(
                    path, {'request_id': rid()}, format='json')
                self.assertEqual(denied.status_code, 403, denied.data)
        pending = self.detail(handover['id'])
        self.assertEqual(pending.data['status'], 'pending')
        self.assertEqual(len(pending.data['events']), 1)

        received = self.api_client(self.receiver, self.company).post(
            path, {'request_id': rid()}, format='json')
        self.assertEqual(received.status_code, 200, received.data)
        self.assertEqual(received.data['receiver'], self.receiver.id)
        self.assertEqual(received.data['received_by'], self.receiver.id)
        self.assertEqual(received.data['received_by_name'], '一组接收人')

    def test_discrepancy_is_independent_and_unlisted_items_are_fully_received(self):
        handover = self.create_handover_batch([{
            'receiver': self.receiver.id, 'target_group_no': 1,
            'items': [
                {'ticket': self.tickets[0].id, 'quantity': 50},
                {'ticket': self.tickets[1].id, 'quantity': 60},
            ],
        }])['items'][0]
        short_item = next(item for item in handover['items']
                          if item['ticket'] == self.tickets[0].id)
        full_item = next(item for item in handover['items']
                         if item['ticket'] == self.tickets[1].id)
        path = f"/api/handovers/{handover['id']}/receive"
        missing_reason = self.api_client(self.receiver, self.company).post(path, {
            'request_id': rid(),
            'items': [{'id': short_item['id'], 'received_quantity': 45,
                       'difference_type': 'shortage', 'reason': ''}],
        }, format='json')
        self.assertEqual(missing_reason.status_code, 400, missing_reason.data)

        response = self.api_client(self.receiver, self.company).post(path, {
            'request_id': rid(), 'reason': '整批差异说明',
            'items': [{'id': short_item['id'], 'received_quantity': 45,
                       'difference_type': 'shortage', 'reason': '短少5件'}],
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'received')
        self.assertTrue(response.data['has_discrepancy'])
        self.assertEqual(response.data['total_quantity'], 110)
        self.assertEqual(response.data['received_quantity'], 105)
        items = {item['id']: item for item in response.data['items']}
        self.assertEqual(items[short_item['id']]['received_quantity'], 45)
        self.assertEqual(items[short_item['id']]['difference_type'], 'shortage')
        self.assertEqual(items[short_item['id']]['reason'], '短少5件')
        self.assertEqual(items[full_item['id']]['received_quantity'], 60)
        self.assertEqual(items[full_item['id']]['difference_type'], '')
        self.assertEqual(items[full_item['id']]['reason'], '')

    def test_reject_requires_reason_and_preserves_rejection_fact(self):
        handover = self.create_handover_batch()['items'][0]
        path = f"/api/handovers/{handover['id']}/reject"
        missing = self.api_client(self.receiver, self.company).post(
            path, {'request_id': rid(), 'reason': ''}, format='json')
        self.assertEqual(missing.status_code, 400, missing.data)
        response = self.api_client(self.receiver, self.company).post(
            path, {'request_id': rid(), 'reason': '菲票与实物不符'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'rejected')
        self.assertEqual(response.data['reject_reason'], '菲票与实物不符')
        self.assertIsNone(response.data['received_by'])
        self.assertIsNone(response.data['received_at'])
        self.assertEqual(response.data['received_quantity'], 0)
        self.assertEqual([event['action'] for event in response.data['events']],
                         ['created', 'rejected'])
        self.assertEqual(response.data['events'][-1]['actor'], self.receiver.id)
        self.assertEqual(response.data['events'][-1]['reason'], '菲票与实物不符')

        late_receive = self.api_client(self.receiver, self.company).post(
            f"/api/handovers/{handover['id']}/receive", {'request_id': rid()}, format='json')
        self.assertEqual(late_receive.status_code, 400, late_receive.data)

    def test_reassign_requires_manage_and_retains_before_after_events(self):
        handover = self.create_handover_batch()['items'][0]
        path = f"/api/handovers/{handover['id']}/reassign"
        denied = self.api_client(self.receiver, self.company).post(path, {
            'request_id': rid(), 'receiver': self.new_receiver.id, 'reason': '调整责任人',
        }, format='json')
        self.assertEqual(denied.status_code, 403, denied.data)
        missing_reason = self.api_client(self.manager, self.company).post(path, {
            'request_id': rid(), 'receiver': self.new_receiver.id, 'reason': '',
        }, format='json')
        self.assertEqual(missing_reason.status_code, 400, missing_reason.data)
        reassigned = self.api_client(self.manager, self.company).post(path, {
            'request_id': rid(), 'receiver': self.new_receiver.id, 'reason': '调整责任人',
        }, format='json')
        self.assertEqual(reassigned.status_code, 200, reassigned.data)
        self.assertEqual(reassigned.data['status'], 'pending')
        self.assertEqual(reassigned.data['receiver'], self.new_receiver.id)
        self.assertIsNone(reassigned.data['received_by'])
        self.assertEqual([event['action'] for event in reassigned.data['events']],
                         ['created', 'reassigned'])
        event = reassigned.data['events'][-1]
        self.assertEqual(event['actor'], self.manager.id)
        self.assertEqual(event['reason'], '调整责任人')
        self.assertEqual(event['before']['receiver'], self.receiver.id)
        self.assertEqual(event['after']['receiver'], self.new_receiver.id)

        old_receiver = self.api_client(self.receiver, self.company).post(
            f"/api/handovers/{handover['id']}/receive", {'request_id': rid()}, format='json')
        self.assertEqual(old_receiver.status_code, 403, old_receiver.data)
        received = self.api_client(self.new_receiver, self.company).post(
            f"/api/handovers/{handover['id']}/receive", {'request_id': rid()}, format='json')
        self.assertEqual(received.status_code, 200, received.data)
        self.assertEqual(received.data['receiver'], self.new_receiver.id)
        self.assertEqual(received.data['received_by'], self.new_receiver.id)
        self.assertEqual([event['action'] for event in received.data['events']],
                         ['created', 'reassigned', 'received'])

    def test_create_idempotency_returns_original_batch_and_conflicts_on_other_payload(self):
        payload = self.create_payload([
            self.target(self.tickets[0], 50, receiver=self.receiver, group=1),
            self.target(self.tickets[1], 60, receiver=None, group=2),
        ], request_id='create-idempotency-contract', remark='原完整载荷')
        first = self.create_handover_batch(payload=payload)
        retry = self.api_client(self.creator, self.company).post(
            '/api/handovers/', deepcopy(payload), format='json')
        self.assertIn(retry.status_code, (200, 201), retry.data)
        self.assertEqual(retry.data['batch_no'], first['batch_no'])
        self.assertEqual(retry.data, first)

        changed = deepcopy(payload)
        changed['targets'][0]['items'][0]['quantity'] = 49
        conflict = self.api_client(self.creator, self.company).post(
            '/api/handovers/', changed, format='json')
        self.assertEqual(conflict.status_code, 409, conflict.data)
        listed = self.api_client(self.creator, self.company).get(
            '/api/handovers/', {'box': 'created', 'batch_no': first['batch_no']})
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data['count'], 2)
        for row in first['items']:
            detail = self.detail(row['id'])
            self.assertEqual([event['action'] for event in detail.data['events']], ['created'])

    def test_repeated_identical_receive_does_not_append_events_or_change_fact(self):
        handover = self.create_handover_batch()['items'][0]
        path = f"/api/handovers/{handover['id']}/receive"
        payload = {'request_id': 'receive-idempotency-contract'}
        first = self.api_client(self.receiver, self.company).post(
            path, deepcopy(payload), format='json')
        self.assertEqual(first.status_code, 200, first.data)
        retry = self.api_client(self.receiver, self.company).post(
            path, deepcopy(payload), format='json')
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(retry.data, first.data)
        detail = self.detail(handover['id'])
        self.assertEqual(detail.data['received_at'], first.data['received_at'])
        self.assertEqual([event['action'] for event in detail.data['events']],
                         ['created', 'received'])

        for method in ('patch', 'delete'):
            with self.subTest(method=method):
                request = getattr(self.api_client(self.manager, self.company), method)
                kwargs = {'format': 'json'} if method == 'patch' else {}
                data = {'remark': '不得修改原事实'} if method == 'patch' else None
                blocked = request(f"/api/handovers/{handover['id']}/", data, **kwargs)
                self.assertIn(blocked.status_code, (403, 405), blocked.data)
        unchanged = self.detail(handover['id'])
        self.assertEqual(unchanged.data, detail.data)

    def test_unauthorized_wrong_group_suspended_and_foreign_company_cannot_receive(self):
        handover = self.create_handover_batch([
            self.target(self.tickets[0], 50, receiver=None, group=1)])['items'][0]
        path = f"/api/handovers/{handover['id']}/receive"
        for user, expected in (
                (self.plain, 403),
                (self.other_group_receiver, 403),
                (self.suspended, 403)):
            with self.subTest(user=user.username):
                response = self.api_client(user, self.company).post(
                    path, {'request_id': rid()}, format='json')
                self.assertEqual(response.status_code, expected, response.data)
        foreign = self.api_client(self.other_admin, self.other_company).post(
            path, {'request_id': rid()}, format='json')
        self.assertEqual(foreign.status_code, 404, foreign.data)
        pending = self.detail(handover['id'])
        self.assertEqual(pending.data['status'], 'pending')
        self.assertIsNone(pending.data['received_by'])
        self.assertEqual([event['action'] for event in pending.data['events']], ['created'])

    def test_batch_summary_is_recomputed_from_current_handovers(self):
        batch = self.create_handover_batch([
            self.target(self.tickets[0], 50, receiver=self.receiver, group=1),
            self.target(self.tickets[1], 60, receiver=None, group=2),
            self.target(self.tickets[2], 30, receiver=self.receiver, group=1),
        ])
        path = f"/api/handovers/batches/{batch['batch_no']}/"
        initial = self.api_client(self.manager, self.company).get(path)
        self.assertEqual(initial.status_code, 200, initial.data)
        self.assertEqual(initial.data['batch_no'], batch['batch_no'])
        self.assertEqual(len(initial.data['items']), 3)
        self.assertEqual(initial.data['summary'], {
            'handover_count': 3, 'received_count': 0, 'pending_count': 3,
            'rejected_count': 0, 'total_quantity': 140, 'received_quantity': 0,
        })

        first, pending, third = batch['items']
        received = self.api_client(self.receiver, self.company).post(
            f"/api/handovers/{first['id']}/receive", {'request_id': rid()}, format='json')
        self.assertEqual(received.status_code, 200, received.data)
        rejected = self.api_client(self.receiver, self.company).post(
            f"/api/handovers/{third['id']}/reject",
            {'request_id': rid(), 'reason': '本单拒收'}, format='json')
        self.assertEqual(rejected.status_code, 200, rejected.data)

        current = self.api_client(self.manager, self.company).get(path)
        self.assertEqual(current.status_code, 200, current.data)
        self.assertEqual({row['id']: row['status'] for row in current.data['items']}, {
            first['id']: 'received', pending['id']: 'pending', third['id']: 'rejected',
        })
        self.assertEqual(current.data['summary'], {
            'handover_count': 3, 'received_count': 1, 'pending_count': 1,
            'rejected_count': 1, 'total_quantity': 140, 'received_quantity': 50,
        })

    def test_handover_company_isolation_covers_people_tickets_nodes_and_reads(self):
        base = self.create_payload([
            self.target(self.other_tickets[0], 40, receiver=self.other_admin)])
        base['source_node'] = self.other_source_node.id
        base['target_node'] = self.other_target_node.id
        invalid_payloads = []
        foreign_ticket = deepcopy(base)
        foreign_ticket['targets'][0]['items'][0]['ticket'] = self.tickets[0].id
        invalid_payloads.append(foreign_ticket)
        foreign_receiver = deepcopy(base)
        foreign_receiver['targets'][0]['receiver'] = self.receiver.id
        invalid_payloads.append(foreign_receiver)
        foreign_node = deepcopy(base)
        foreign_node['source_node'] = self.source_node.id
        invalid_payloads.append(foreign_node)
        for payload in invalid_payloads:
            payload['request_id'] = rid()
            with self.subTest(payload=payload):
                response = self.api_client(self.other_admin, self.other_company).post(
                    '/api/handovers/', payload, format='json')
                self.assertEqual(response.status_code, 404, response.data)

        handover = self.create_handover_batch()['items'][0]
        foreign_detail = self.detail(
            handover['id'], user=self.other_admin, company=self.other_company)
        self.assertEqual(foreign_detail.status_code, 404, foreign_detail.data)
        foreign_batch = self.api_client(self.other_admin, self.other_company).get(
            f"/api/handovers/batches/{handover['batch_no']}/")
        self.assertEqual(foreign_batch.status_code, 404, foreign_batch.data)
