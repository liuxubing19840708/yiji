"""运行：DJANGO_SETTINGS_MODULE=apps.handover.tests.test_settings .venv/Scripts/python manage.py test apps.handover"""
from uuid import uuid4
from unittest.mock import patch
from datetime import timedelta
import io

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from apps.accounts.models import Company, Post, User, UserCompany
from apps.catalog.models import Style, Ticket
from apps.cutting.services import create_handover as create_legacy_handover, sign_handover
from apps.handover.exceptions import IdempotencyError, NotFound, ValidationError
from apps.handover.models import Handover, HandoverEvent, HandoverItem, HandoverPolicy
from apps.handover.services import (create_handovers, effective_policy, receive_handover,
                                    reassign_handover, reject_handover)
from apps.manufacturing.exceptions import QuantityError, TransitionError
from apps.manufacturing.models import ProductionRoute, ProductionStage, QuantityLedger, RouteNode
from apps.manufacturing.services import (complete_stage, get_balance, receive_stage,
                                         transfer_stage)
from apps.ops.models import Notification
from apps.production.models import Production


def rid():
    return uuid4().hex


class HandoverTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = cls.make_user('owner', '交出人')
        cls.receiver = cls.make_user('receiver', '接收人')
        cls.group2_user = cls.make_user('group2', '二组接收人')
        cls.other_receiver = cls.make_user('other-receiver', '改派接收人')
        cls.outsider = cls.make_user('outsider', '无接收权限')
        cls.company = Company.objects.create(name='交接一厂', company_no='920001', creator=cls.owner)
        cls.other_company = Company.objects.create(name='交接二厂', company_no='920002', creator=cls.owner)
        manager = Post.objects.create(company=cls.company, name='交接管理', permissions=[
            'handover.view', 'handover.create', 'handover.receive', 'handover.manage'])
        worker = Post.objects.create(company=cls.company, name='接收员', permissions=[
            'handover.view', 'handover.receive'])
        viewer = Post.objects.create(company=cls.company, name='查看员', permissions=['handover.view'])
        other_manager = Post.objects.create(company=cls.other_company, name='二厂管理', permissions=['*'])
        for user, post, group in [(cls.owner, manager, 1), (cls.receiver, worker, 1),
                                  (cls.group2_user, worker, 2), (cls.other_receiver, worker, 1),
                                  (cls.outsider, viewer, 1)]:
            UserCompany.objects.create(company=cls.company, user=user, post=post, group_no=group,
                                       status=UserCompany.Status.ACTIVE)
        UserCompany.objects.create(company=cls.other_company, user=cls.owner, post=other_manager,
                                   status=UserCompany.Status.ACTIVE)
        cls.style = Style.objects.create(company=cls.company, style_no='HO-STYLE', name='交接款')
        cls.bed = Production.objects.create(company=cls.company, style=cls.style,
                                            bed_no='HO-BED', total_qty=100)
        cls.ticket1 = Ticket.objects.create(company=cls.company, production=cls.bed,
                                            bed_no=cls.bed.bed_no, bundle_no='1', color='红', qty=50,
                                            qr_code='HO-1')
        cls.ticket2 = Ticket.objects.create(company=cls.company, production=cls.bed,
                                            bed_no=cls.bed.bed_no, bundle_no='2', color='蓝', qty=60,
                                            qr_code='HO-2')
        cls.route = ProductionRoute.objects.create(company=cls.company, code='HO-ROUTE',
                                                   name='交接路线', production=cls.bed)
        cut = ProductionStage.objects.create(company=cls.company, code='cut', name='裁剪',
                                             stage_type=ProductionStage.StageType.CUTTING)
        sew = ProductionStage.objects.create(company=cls.company, code='sew', name='缝制',
                                             stage_type=ProductionStage.StageType.SEWING)
        cls.source = RouteNode.objects.create(company=cls.company, route=cls.route, stage=cut, sequence=1)
        cls.target = RouteNode.objects.create(company=cls.company, route=cls.route, stage=sew, sequence=2)
        for ticket in (cls.ticket1, cls.ticket2):
            receive_stage(cls.company, cls.source, ticket, ticket.qty,
                          operator=cls.owner, request_id=rid())
            complete_stage(cls.company, cls.source, ticket, ticket.qty,
                           operator=cls.owner, request_id=rid())

    @staticmethod
    def make_user(username, real_name):
        return User.objects.create_user(username=f'ho-{username}', real_name=real_name,
                                        phone=None, password='password')

    def payload(self, **overrides):
        data = {'kind': 'cutting', 'request_id': rid(), 'source_node': self.source.id,
                'target_node': self.target.id, 'remark': '首批交接', 'targets': [
                    {'receiver': self.receiver.id, 'target_group_no': 1,
                     'items': [{'ticket': self.ticket1.id, 'quantity': 50}]},
                    {'receiver': None, 'target_group_no': 2,
                     'items': [{'ticket': self.ticket2.id, 'quantity': 60}]},
                ]}
        data.update(overrides)
        return data

    def create(self, **overrides):
        data = self.payload(**overrides)
        return create_handovers(self.company, created_by=self.owner, **data)

    def api_client(self, user, company=None):
        client = APIClient()
        client.force_authenticate(user)
        client.credentials(HTTP_X_COMPANY_ID=str((company or self.company).id))
        return client

    def test_batch_idempotency_no_ledger_and_duplicate_ticket(self):
        data = self.payload()
        rows = create_handovers(self.company, created_by=self.owner, **data)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row.batch_no for row in rows}), 1)
        self.assertEqual(rows[0].total_quantity, 50)
        self.assertEqual(rows[0].events.get().action, 'created')
        self.assertEqual(QuantityLedger.objects.count(), 4)
        repeated = create_handovers(self.company, created_by=self.owner, **data)
        self.assertEqual([row.id for row in repeated], [row.id for row in rows])
        self.assertEqual(Handover.objects.count(), 2)
        self.assertEqual(HandoverEvent.objects.filter(action='created').count(), 2)
        changed = {**data, 'remark': '不同载荷'}
        with self.assertRaises(IdempotencyError):
            create_handovers(self.company, created_by=self.owner, **changed)
        duplicate = self.payload(targets=[
            {'receiver': self.receiver.id, 'items': [{'ticket': self.ticket1.id, 'quantity': 1}]},
            {'target_group_no': 2, 'items': [{'ticket': self.ticket1.id, 'quantity': 1}]},
        ])
        with self.assertRaises(ValidationError):
            create_handovers(self.company, created_by=self.owner, **duplicate)
        invalid = self.payload(targets=[{'target_group_no': 1,
                                        'items': [{'ticket': self.ticket1.id, 'quantity': 0}]}])
        with self.assertRaises(ValidationError):
            create_handovers(self.company, created_by=self.owner, **invalid)

    def test_company_isolation_for_create_detail_and_action(self):
        rows = self.create()
        with self.assertRaises(NotFound):
            create_handovers(self.other_company, created_by=self.owner, **self.payload())
        with self.assertRaises(NotFound):
            receive_handover(self.other_company, rows[0].id, request_id=rid(), actor=self.owner)
        other_client = self.api_client(self.owner, self.other_company)
        self.assertEqual(other_client.get(f'/api/handovers/{rows[0].id}/').status_code, 404)
        self.assertEqual(other_client.get(f"/api/handovers/batches/{rows[0].batch_no}/").status_code, 404)

    def test_designated_receiver_and_receive_idempotency(self):
        handover = self.create()[0]
        with self.assertRaises(PermissionDenied):
            receive_handover(self.company, handover.id, request_id=rid(), actor=self.group2_user)
        with self.assertRaises(PermissionDenied):
            receive_handover(self.company, handover.id, request_id=rid(), actor=self.outsider)
        request_id = rid()
        received = receive_handover(self.company, handover.id, request_id=request_id,
                                    actor=self.receiver)
        self.assertEqual(received.status, 'received')
        self.assertEqual(received.received_by, self.receiver)
        self.assertEqual(received.received_quantity, 50)
        self.assertFalse(received.has_discrepancy)
        self.assertIsNotNone(received.received_at)
        again = receive_handover(self.company, handover.id, request_id=request_id,
                                 actor=self.receiver)
        self.assertEqual(again.id, received.id)
        self.assertEqual(handover.events.filter(action='received').count(), 1)
        with self.assertRaises(IdempotencyError):
            receive_handover(self.company, handover.id, request_id=request_id,
                             actor=self.receiver, reason='不同载荷')
        self.assertEqual(QuantityLedger.objects.count(), 6)

    def test_unassigned_requires_matching_group_and_records_actual_receiver(self):
        handover = self.create()[1]
        with self.assertRaises(PermissionDenied):
            receive_handover(self.company, handover.id, request_id=rid(), actor=self.receiver)
        received = receive_handover(self.company, handover.id, request_id=rid(), actor=self.group2_user)
        self.assertIsNone(received.receiver)
        self.assertEqual(received.received_by, self.group2_user)
        event = received.events.get(action='received')
        self.assertEqual(event.actor, self.group2_user)
        self.assertEqual(event.actual_user, self.group2_user)

    def test_discrepancy_zero_quantity_validation_and_atomicity(self):
        handover = self.create()[0]
        item = handover.items.get()
        with self.assertRaises(ValidationError):
            receive_handover(self.company, handover.id, request_id=rid(), actor=self.receiver,
                             items=[{'id': item.id, 'received_quantity': 45,
                                     'difference_type': 'shortage', 'reason': ''}])
        handover.refresh_from_db()
        self.assertEqual(handover.status, 'pending')
        self.assertEqual(handover.items.get().received_quantity, 0)
        with self.assertRaises(ValidationError):
            receive_handover(self.company, handover.id, request_id=rid(), actor=self.receiver,
                             items=[{'id': item.id, 'received_quantity': 51,
                                     'difference_type': 'overage', 'reason': '超收'}])
        received = receive_handover(
            self.company, handover.id, request_id=rid(), actor=self.receiver, reason='整批复核',
            items=[{'id': item.id, 'received_quantity': 0,
                    'difference_type': 'shortage', 'reason': '整扎短少'}])
        self.assertEqual(received.status, 'received')
        self.assertEqual(received.received_quantity, 0)
        self.assertTrue(received.has_discrepancy)
        item.refresh_from_db()
        self.assertEqual(item.reason, '整扎短少')

    def test_reject_and_reassign_preserve_events_and_idempotency(self):
        rejected = self.single(self.ticket1, 50)[0]
        request_id = rid()
        reject_handover(self.company, rejected.id, request_id=request_id,
                        actor=self.receiver, reason='菲票不符')
        reject_handover(self.company, rejected.id, request_id=request_id,
                        actor=self.receiver, reason='菲票不符')
        rejected.refresh_from_db()
        self.assertEqual(rejected.status, 'rejected')
        self.assertEqual(rejected.reject_reason, '菲票不符')
        self.assertEqual(rejected.events.filter(action='rejected').count(), 1)
        with self.assertRaises(ValidationError):
            reassign_handover(self.company, rejected.id, request_id=rid(), actor=self.owner,
                              receiver=self.other_receiver.id, reason='改派')

        pending = self.single(self.ticket1, 50)[0]
        with self.assertRaises(PermissionDenied):
            reassign_handover(self.company, pending.id, request_id=rid(), actor=self.receiver,
                              receiver=self.other_receiver.id, reason='无权限')
        request_id = rid()
        changed = reassign_handover(self.company, pending.id, request_id=request_id, actor=self.owner,
                                    receiver=self.other_receiver.id, reason='休假改派')
        self.assertEqual(changed.receiver, self.other_receiver)
        reassign_handover(self.company, pending.id, request_id=request_id, actor=self.owner,
                          receiver=self.other_receiver.id, reason='休假改派')
        event = pending.events.get(action='reassigned')
        self.assertEqual(event.before['receiver'], self.receiver.id)
        self.assertEqual(event.after['receiver'], self.other_receiver.id)
        self.assertEqual(pending.events.filter(action='reassigned').count(), 1)
        membership = UserCompany.objects.get(company=self.company, user=self.other_receiver)
        membership.is_suspended = True
        membership.save(update_fields=['is_suspended', 'updated_at'])
        another = self.single(self.ticket2, 60)[0]
        with self.assertRaises(ValidationError):
            reassign_handover(self.company, another.id, request_id=rid(), actor=self.owner,
                              receiver=self.other_receiver.id, reason='停职')

    def test_policy_stores_strict_without_generic_gate_and_checks_company(self):
        client = self.api_client(self.owner)
        response = client.post('/api/handovers/policies', {
            'kind': 'cutting', 'mode': 'strict', 'source_node': self.source.id,
            'target_node': self.target.id, 'target_group_no': 2,
            'receiver': self.group2_user.id, 'timeout_minutes': 90,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['mode'], 'strict')
        self.assertEqual(response.data['updated_by_name'], '交出人')
        policy_id = response.data['id']
        response = client.put(f'/api/handovers/policies/{policy_id}', {
            'kind': 'cutting', 'mode': 'off', 'timeout_minutes': 120,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data['source_node'])
        # 通用创建只预占；strict 的直接转出门禁由第二批集成测试覆盖。
        self.create()
        self.assertEqual(QuantityLedger.objects.count(), 4)
        other_client = self.api_client(self.owner, self.other_company)
        self.assertEqual(other_client.put(f'/api/handovers/policies/{policy_id}', {
            'kind': 'cutting', 'mode': 'off', 'timeout_minutes': 120,
        }, format='json').status_code, 404)
        denied = self.api_client(self.receiver).post('/api/handovers/policies', {
            'kind': 'cutting', 'mode': 'off', 'timeout_minutes': 120,
        }, format='json')
        self.assertEqual(denied.status_code, 403)

    def test_api_filters_batch_summary_events_and_no_patch_delete(self):
        data = self.payload()
        client = self.api_client(self.owner)
        response = client.post('/api/handovers/', data, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        batch_no = response.data['batch_no']
        rows = response.data['items']
        receiver_client = self.api_client(self.receiver)
        mine = receiver_client.get('/api/handovers/?box=mine')
        self.assertEqual(mine.status_code, 200, mine.data)
        self.assertEqual([row['id'] for row in mine.data['results']], [rows[0]['id']])
        created = receiver_client.get('/api/handovers/?box=created')
        self.assertEqual(created.data['count'], 0)
        received = receiver_client.post(f"/api/handovers/{rows[0]['id']}/receive",
                                        {'request_id': rid()}, format='json')
        self.assertEqual(received.status_code, 200, received.data)
        self.assertEqual(received.data['total_quantity'], 50)
        self.assertEqual(received.data['items'][0]['ticket_label'], 'HO-BED/1/红')
        self.assertEqual([event['action'] for event in received.data['events']],
                         ['created', 'received'])
        batch = client.get(f'/api/handovers/batches/{batch_no}/')
        self.assertEqual(batch.status_code, 200, batch.data)
        self.assertEqual(batch.data['summary']['handover_count'], 2)
        self.assertEqual(batch.data['summary']['received_count'], 1)
        self.assertEqual(batch.data['summary']['pending_count'], 1)
        self.assertEqual(batch.data['summary']['total_quantity'], 110)
        self.assertEqual(batch.data['summary']['received_quantity'], 50)
        self.assertEqual(client.patch(f"/api/handovers/{rows[0]['id']}/", {}, format='json').status_code, 405)
        self.assertEqual(client.delete(f"/api/handovers/{rows[0]['id']}/").status_code, 405)

    def single(self, ticket, quantity, **overrides):
        target = {'receiver': self.receiver.id,
                  'items': [{'ticket': ticket.id, 'quantity': quantity}]}
        target.update(overrides)
        return self.create(targets=[target])

    def legacy(self, ticket, quantity):
        return create_legacy_handover(
            self.company, ticket_id=ticket.id, source_node_id=self.source.id,
            target_node_id=self.target.id, receiver_id=self.receiver.id,
            quantity=quantity, request_id=rid(), created_by=self.owner)

    def test_new_and_legacy_pending_reservations_are_mutually_visible(self):
        generic = self.single(self.ticket1, 50)[0]
        with self.assertRaises(QuantityError):
            self.legacy(self.ticket1, 1)
        reject_handover(self.company, generic.id, request_id=rid(),
                        actor=self.receiver, reason='释放预占')
        legacy = self.legacy(self.ticket1, 25)
        with self.assertRaises(ValidationError):
            self.single(self.ticket1, 26)
        self.assertEqual(legacy.status, 'pending')
        self.single(self.ticket1, 25)

    def test_multi_target_overallocation_rolls_back_entire_batch(self):
        before = QuantityLedger.objects.count()
        data = self.payload(targets=[
            {'receiver': self.receiver.id,
             'items': [{'ticket': self.ticket1.id, 'quantity': 50}]},
            {'receiver': self.receiver.id,
             'items': [{'ticket': self.ticket2.id, 'quantity': 61}]},
        ])
        with self.assertRaises(ValidationError):
            create_handovers(self.company, created_by=self.owner, **data)
        self.assertEqual(Handover.objects.count(), 0)
        self.assertEqual(HandoverItem.objects.count(), 0)
        self.assertEqual(QuantityLedger.objects.count(), before)

    def test_actual_receipt_writes_linked_ledgers_and_shortage_stays_at_source(self):
        handover = self.create(targets=[{'receiver': self.receiver.id, 'items': [
            {'ticket': self.ticket1.id, 'quantity': 20},
            {'ticket': self.ticket2.id, 'quantity': 30},
        ]}])[0]
        first = handover.items.get(ticket=self.ticket1)
        before = QuantityLedger.objects.count()
        receive_handover(self.company, handover.id, request_id=rid(), actor=self.receiver,
                         items=[{'id': first.id, 'received_quantity': 15,
                                 'difference_type': 'shortage', 'reason': '短少5件'}])
        first.refresh_from_db()
        second = handover.items.get(ticket=self.ticket2)
        self.assertEqual((first.out_ledger.quantity, first.in_ledger.quantity), (15, 15))
        self.assertEqual(first.out_ledger.node, self.source)
        self.assertEqual(first.in_ledger.node, self.target)
        self.assertEqual(first.in_ledger.related_entry, first.out_ledger)
        self.assertEqual(second.out_ledger.quantity, 30)
        self.assertEqual(get_balance(self.company, self.source, self.ticket1)['available'], 35)
        self.assertEqual(get_balance(self.company, self.source, self.ticket1)['transferable'], 35)
        self.assertEqual(QuantityLedger.objects.count(), before + 4)

    def test_overage_receipt_uses_real_available_quantity_and_links_ledgers(self):
        handover = self.single(self.ticket1, 5)[0]
        item = handover.items.get()
        before = QuantityLedger.objects.count()
        receive_handover(self.company, handover.id, request_id=rid(), actor=self.receiver,
                         items=[{'id': item.id, 'received_quantity': 8,
                                 'difference_type': 'overage', 'reason': '原单少计3件'}])
        item.refresh_from_db()
        self.assertEqual(item.received_quantity, 8)
        self.assertEqual(item.out_ledger.quantity, 8)
        self.assertEqual(item.in_ledger.quantity, 8)
        self.assertEqual(get_balance(self.company, self.source, self.ticket1)['available'], 42)
        self.assertEqual(QuantityLedger.objects.count(), before + 2)

        other = self.single(self.ticket2, 60)[0]
        other_item = other.items.get()
        with self.assertRaises(ValidationError):
            receive_handover(self.company, other.id, request_id=rid(), actor=self.receiver,
                             items=[{'id': other_item.id, 'received_quantity': 61,
                                     'difference_type': 'overage', 'reason': '无库存超收'}])
        other.refresh_from_db()
        self.assertEqual(other.status, 'pending')
        self.assertEqual(QuantityLedger.objects.count(), before + 2)

    def test_reassign_keeps_pending_reservation(self):
        handover = self.single(self.ticket1, 50)[0]
        before = QuantityLedger.objects.count()
        reassign_handover(self.company, handover.id, request_id=rid(), actor=self.owner,
                          receiver=self.other_receiver.id, reason='改派仍待接收')
        with self.assertRaises(ValidationError):
            self.single(self.ticket1, 1)
        self.assertEqual(QuantityLedger.objects.count(), before)
        handover.refresh_from_db()
        self.assertEqual(handover.status, 'pending')

    def test_zero_receipt_has_no_ledger_and_rejection_reserves_nothing(self):
        handover = self.single(self.ticket1, 10)[0]
        item = handover.items.get()
        before = QuantityLedger.objects.count()
        receive_handover(self.company, handover.id, request_id=rid(), actor=self.receiver,
                         items=[{'id': item.id, 'received_quantity': 0,
                                 'difference_type': 'shortage', 'reason': '整扎短少'}])
        item.refresh_from_db()
        self.assertIsNone(item.out_ledger)
        self.assertIsNone(item.in_ledger)
        self.assertEqual(QuantityLedger.objects.count(), before)
        self.assertEqual(get_balance(self.company, self.source, self.ticket1)['available'], 50)

        rejected = self.single(self.ticket1, 10)[0]
        request_id = rid()
        reject_handover(self.company, rejected.id, request_id=request_id,
                        actor=self.receiver, reason='拒收')
        reject_handover(self.company, rejected.id, request_id=request_id,
                        actor=self.receiver, reason='拒收')
        self.assertEqual(QuantityLedger.objects.count(), before)
        self.assertEqual(rejected.events.filter(action='rejected').count(), 1)
        self.single(self.ticket1, 50)

    def test_repeated_receive_does_not_duplicate_ledgers(self):
        handover = self.single(self.ticket1, 8)[0]
        request_id = rid()
        before = QuantityLedger.objects.count()
        received = receive_handover(self.company, handover.id, request_id=request_id,
                                    actor=self.receiver)
        repeated = receive_handover(self.company, handover.id, request_id=request_id,
                                    actor=self.receiver)
        self.assertEqual(received.items.get().out_ledger_id, repeated.items.get().out_ledger_id)
        self.assertEqual(QuantityLedger.objects.count(), before + 2)
        self.assertEqual(handover.events.filter(action='received').count(), 1)

    def test_multi_item_failure_rolls_back_all_ledgers_and_receipt_facts(self):
        from apps.handover import services as handover_services
        handover = self.create(targets=[{'receiver': self.receiver.id, 'items': [
            {'ticket': self.ticket1.id, 'quantity': 10},
            {'ticket': self.ticket2.id, 'quantity': 10},
        ]}])[0]
        before = QuantityLedger.objects.count()
        original = handover_services.transfer_stage
        calls = []
        def fail_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError('虚拟第二行失败')
            return original(*args, **kwargs)
        with patch('apps.handover.services.transfer_stage', side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                receive_handover(self.company, handover.id, request_id=rid(), actor=self.receiver)
        handover.refresh_from_db()
        self.assertEqual(handover.status, 'pending')
        self.assertEqual(QuantityLedger.objects.count(), before)
        self.assertEqual(handover.items.filter(received_quantity__gt=0).count(), 0)
        self.assertEqual(handover.items.filter(out_ledger__isnull=False).count(), 0)
        self.assertFalse(handover.events.filter(action='received').exists())

    def test_strict_blocks_direct_cutting_transfer_but_allows_both_receipts(self):
        HandoverPolicy.objects.create(company=self.company, kind='cutting', mode='strict')
        with self.assertRaises(TransitionError):
            transfer_stage(self.company, self.source, self.target, self.ticket1, 1,
                           operator=self.owner, request_id=rid())
        generic = self.single(self.ticket1, 4)[0]
        receive_handover(self.company, generic.id, request_id=rid(), actor=self.receiver)
        legacy = self.legacy(self.ticket1, 4)
        sign_handover(self.company, legacy.id, actor=self.receiver)
        self.assertEqual(get_balance(self.company, self.target, self.ticket1)['received'], 8)

    def test_strict_does_not_block_first_inbound_completion_or_non_cutting_transfer(self):
        HandoverPolicy.objects.create(company=self.company, kind='cutting', mode='strict')
        ticket = Ticket.objects.create(company=self.company, production=self.bed,
                                       bed_no=self.bed.bed_no, bundle_no='strict', qty=5,
                                       qr_code='HO-STRICT')
        receive_stage(self.company, self.source, ticket, 5,
                      operator=self.owner, request_id=rid())
        complete_stage(self.company, self.source, ticket, 5,
                       operator=self.owner, request_id=rid())
        handover = self.single(ticket, 5)[0]
        receive_handover(self.company, handover.id, request_id=rid(), actor=self.receiver)
        complete_stage(self.company, self.target, ticket, 5,
                       operator=self.owner, request_id=rid())
        packing = ProductionStage.objects.create(
            company=self.company, code='strict-pack', name='包装',
            stage_type=ProductionStage.StageType.PACKING)
        pack = RouteNode.objects.create(company=self.company, route=self.route,
                                        stage=packing, sequence=3)
        transfer_stage(self.company, self.target, pack, ticket, 5,
                       operator=self.owner, request_id=rid())
        self.assertEqual(get_balance(self.company, pack, ticket)['received'], 5)

    def test_off_warn_do_not_block_and_other_company_strict_is_isolated(self):
        HandoverPolicy.objects.create(company=self.other_company, kind='cutting', mode='strict')
        transfer_stage(self.company, self.source, self.target, self.ticket1, 1,
                       operator=self.owner, request_id=rid())
        policy = HandoverPolicy.objects.create(company=self.company, kind='cutting', mode='warn')
        transfer_stage(self.company, self.source, self.target, self.ticket1, 1,
                       operator=self.owner, request_id=rid())
        policy.mode = 'off'
        policy.save(update_fields=['mode', 'updated_at'])
        transfer_stage(self.company, self.source, self.target, self.ticket1, 1,
                       operator=self.owner, request_id=rid())
        self.assertEqual(get_balance(self.company, self.target, self.ticket1)['received'], 3)

    def test_policy_node_precedence_and_four_unique_scopes(self):
        default = HandoverPolicy.objects.create(company=self.company, kind='cutting', mode='strict')
        target = HandoverPolicy.objects.create(company=self.company, kind='cutting', mode='strict',
                                               target_node=self.target)
        source = HandoverPolicy.objects.create(company=self.company, kind='cutting', mode='strict',
                                               source_node=self.source)
        pair = HandoverPolicy.objects.create(company=self.company, kind='cutting', mode='off',
                                             source_node=self.source, target_node=self.target)
        selected = effective_policy(self.company, 'cutting', self.source, self.target)
        self.assertEqual(selected, pair)
        transfer_stage(self.company, self.source, self.target, self.ticket1, 1,
                       operator=self.owner, request_id=rid())
        pair.delete()
        self.assertEqual(effective_policy(self.company, 'cutting', self.source, self.target), source)
        source.delete()
        self.assertEqual(effective_policy(self.company, 'cutting', self.source, self.target), target)
        target.delete()
        self.assertEqual(effective_policy(self.company, 'cutting', self.source, self.target), default)
        default.delete()
        self.assertEqual(effective_policy(self.company, 'cutting', self.source, self.target).mode,
                         'off')

    def test_warn_timeout_command_notifies_recipients_once_per_day_and_api_marks_overdue(self):
        HandoverPolicy.objects.create(company=self.company, kind='cutting', mode='warn',
                                      timeout_minutes=30)
        rows = self.create()
        Handover.objects.filter(pk__in=[row.pk for row in rows]).update(
            created_at=timezone.now() - timedelta(minutes=31))
        output = io.StringIO()
        call_command('notify_handover_timeouts', noinput=True, stdout=output)
        notifications = Notification.objects.filter(company=self.company, code='handover_timeout')
        self.assertEqual(notifications.count(), 2)
        self.assertEqual({row.user_id for row in notifications},
                         {self.receiver.id, self.group2_user.id})
        self.assertIn('新通知 2 人', output.getvalue())
        call_command('notify_handover_timeouts', noinput=True, stdout=io.StringIO())
        self.assertEqual(Notification.objects.filter(
            company=self.company, code='handover_timeout').count(), 2)
        response = self.api_client(self.receiver).get(f'/api/handovers/{rows[0].id}/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['policy_mode'], 'warn')
        self.assertTrue(response.data['is_overdue'])
        self.assertGreaterEqual(response.data['overdue_minutes'], 1)
        self.assertEqual(response.data['timeout_minutes'], 30)
        self.assertTrue(response.data['due_at'])

    def test_off_not_yet_due_and_received_do_not_notify(self):
        policy = HandoverPolicy.objects.create(company=self.company, kind='cutting', mode='off',
                                               timeout_minutes=30)
        overdue = self.single(self.ticket1, 5)[0]
        Handover.objects.filter(pk=overdue.pk).update(
            created_at=timezone.now() - timedelta(minutes=31))
        from apps.handover.timeout_services import notify_handover_timeouts
        self.assertEqual(notify_handover_timeouts()['sent'], 0)
        policy.mode = 'warn'
        policy.save(update_fields=['mode', 'updated_at'])
        not_due = self.single(self.ticket2, 5)[0]
        self.assertEqual(notify_handover_timeouts()['sent'], 1)
        receive_handover(self.company, overdue.id, request_id=rid(), actor=self.receiver)
        self.assertEqual(notify_handover_timeouts()['sent'], 0)
        self.assertFalse(Notification.objects.filter(
            company=self.company, code='handover_timeout', title__contains=not_due.handover_no
        ).exists())

    def test_strict_timeout_reassign_notifies_new_recipient_without_duplicating_old(self):
        HandoverPolicy.objects.create(company=self.company, kind='cutting', mode='strict',
                                      timeout_minutes=1)
        handover = self.single(self.ticket1, 5)[0]
        Handover.objects.filter(pk=handover.pk).update(
            created_at=timezone.now() - timedelta(minutes=2))
        from apps.handover.timeout_services import notify_handover_timeouts
        self.assertEqual(notify_handover_timeouts()['sent'], 1)
        reassign_handover(self.company, handover.id, request_id=rid(), actor=self.owner,
                          receiver=self.other_receiver.id, reason='原接收人请假')
        self.assertEqual(notify_handover_timeouts()['sent'], 1)
        self.assertEqual(Notification.objects.filter(
            company=self.company, code='handover_timeout',
            title__contains=handover.handover_no).count(), 2)
        self.assertTrue(Notification.objects.filter(
            company=self.company, code='handover_timeout', user=self.other_receiver).exists())

    def test_timeout_without_eligible_receiver_alerts_manager(self):
        HandoverPolicy.objects.create(company=self.company, kind='cutting', mode='warn',
                                      timeout_minutes=1)
        handover = self.create(targets=[{'receiver': self.outsider.id,
                                         'items': [{'ticket': self.ticket1.id, 'quantity': 5}]}])[0]
        Handover.objects.filter(pk=handover.pk).update(
            created_at=timezone.now() - timedelta(minutes=2))
        from apps.handover.timeout_services import notify_handover_timeouts
        self.assertEqual(notify_handover_timeouts()['sent'], 1)
        notification = Notification.objects.get(company=self.company, code='handover_timeout')
        self.assertEqual(notification.user, self.owner)
        self.assertIn('无有效接收人', notification.message)

    def test_events_are_append_only(self):
        handover = self.create()[0]
        event = handover.events.get()
        event.reason = '篡改'
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()
        self.assertEqual(HandoverEvent.objects.count(), 2)
