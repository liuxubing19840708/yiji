"""运行：.venv/Scripts/python manage.py test apps.finished_goods --settings=apps.finished_goods.tests.test_settings"""
from unittest.mock import patch
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.accounts.models import Company, Post, User, UserCompany
from apps.catalog.models import Style, Ticket
from apps.manufacturing.exceptions import QuantityError
from apps.manufacturing.models import (ProductionRoute, ProductionStage, QuantityLedger,
                                       RouteNode)
from apps.manufacturing.services import get_balance, receive_stage, reverse_quantity
from apps.production.models import Production
from apps.finished_goods.exceptions import FrozenError, IdempotencyError, NotFound, ValidationError
from apps.finished_goods.models import (DocumentStatus, FinishedInbound, FinishedInboundLine,
                                        PackingBox, PackingBoxLine, PackingTask)
from apps.finished_goods.services import (approve_finished_inbound, approve_packing_task,
                                          create_finished_inbound, create_packing_task,
                                          get_inventory, release_finished_stock,
                                          reverse_finished_inbound, reverse_packing_task)


def rid():
    return uuid4().hex


class FinishedGoodsTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='fg-owner', phone='13800000011',
                                            password='password')
        cls.company = Company.objects.create(name='包装测试一厂', company_no='910011',
                                             creator=cls.user)
        cls.other_company = Company.objects.create(name='包装测试二厂', company_no='910012',
                                                   creator=cls.user)
        cls.post = Post.objects.create(company=cls.company, name='包装管理员',
                                       permissions=['finished_goods.view', 'finished_goods.manage'])
        cls.membership = UserCompany.objects.create(user=cls.user, company=cls.company,
                                                    post=cls.post, status=UserCompany.Status.ACTIVE)
        cls.style = Style.objects.create(company=cls.company, style_no='FG-S1', name='测试款')
        cls.bed = Production.objects.create(company=cls.company, style=cls.style,
                                            bed_no='FG-B1', total_qty=16)
        cls.ticket = Ticket.objects.create(company=cls.company, production=cls.bed,
                                           bed_no=cls.bed.bed_no, bundle_no='1', color='黑',
                                           size='M', qty=10, qr_code='FG-B1-1')
        cls.ticket2 = Ticket.objects.create(company=cls.company, production=cls.bed,
                                            bed_no=cls.bed.bed_no, bundle_no='2', color='白',
                                            size='L', qty=6, qr_code='FG-B1-2')
        cls.route = ProductionRoute.objects.create(company=cls.company, code='FG-R1',
                                                   name='成品路线', production=cls.bed)
        packing_stage = ProductionStage.objects.create(
            company=cls.company, code='pack', name='包装',
            stage_type=ProductionStage.StageType.PACKING)
        inbound_stage = ProductionStage.objects.create(
            company=cls.company, code='inbound', name='成品入库',
            stage_type=ProductionStage.StageType.FINISHED_INBOUND)
        cls.packing = RouteNode.objects.create(company=cls.company, route=cls.route,
                                               stage=packing_stage, sequence=1)
        cls.inbound = RouteNode.objects.create(company=cls.company, route=cls.route,
                                               stage=inbound_stage, sequence=2)

        cls.other_style = Style.objects.create(company=cls.other_company, style_no='FG-OTHER')
        cls.other_bed = Production.objects.create(company=cls.other_company, style=cls.other_style,
                                                  bed_no='FG-OTHER-B', total_qty=5)
        cls.other_ticket = Ticket.objects.create(
            company=cls.other_company, production=cls.other_bed, bed_no=cls.other_bed.bed_no,
            bundle_no='1', qty=5, qr_code='FG-OTHER-B-1')
        cls.other_route = ProductionRoute.objects.create(
            company=cls.other_company, code='FG-OTHER-R', production=cls.other_bed)
        other_pack = ProductionStage.objects.create(
            company=cls.other_company, code='pack', name='包装',
            stage_type=ProductionStage.StageType.PACKING)
        other_in = ProductionStage.objects.create(
            company=cls.other_company, code='in', name='成品入库',
            stage_type=ProductionStage.StageType.FINISHED_INBOUND)
        cls.other_packing = RouteNode.objects.create(company=cls.other_company,
                                                     route=cls.other_route, stage=other_pack,
                                                     sequence=1)
        cls.other_inbound = RouteNode.objects.create(company=cls.other_company,
                                                     route=cls.other_route, stage=other_in,
                                                     sequence=2)

    def receive(self, ticket=None, quantity=10):
        ticket = ticket or self.ticket
        return receive_stage(self.company, self.packing, ticket, quantity,
                             operator=self.user, request_id=rid())

    def packing_payload(self, quantity=6, box_no='FG-BOX-1', ticket=None):
        ticket = ticket or self.ticket
        return {'route_id': self.route.pk,
                'boxes': [{'box_no': box_no,
                           'items': [{'ticket_id': ticket.pk, 'quantity': quantity}]}]}

    def make_task(self, quantity=6, box_no='FG-BOX-1', approve=True, ticket=None):
        task = create_packing_task(company=self.company, operator=self.user, request_id=rid(),
                                   **self.packing_payload(quantity, box_no, ticket))
        if approve:
            approve_packing_task(self.company, task.pk, request_id=rid(), operator=self.user)
            task.refresh_from_db()
        return task

    def make_inbound(self, task, quantity=2, approve=True, warehouse='成品一仓', location='A-01'):
        box = task.boxes.get()
        line = box.lines.get()
        document = create_finished_inbound(
            self.company, route_id=self.route.pk,
            lines=[{'box_id': box.pk, 'ticket_id': line.ticket_id, 'quantity': quantity}],
            request_id=rid(), operator=self.user, warehouse_name=warehouse, location_name=location)
        if approve:
            approve_finished_inbound(self.company, document.pk, request_id=rid(), operator=self.user)
            document.refresh_from_db()
        return document


class FinishedGoodsServiceTests(FinishedGoodsTestBase):
    def test_partial_packing_two_tasks_and_box_snapshots(self):
        self.receive()
        first = self.make_task(quantity=6, box_no='FG-PART-1')
        second = self.make_task(quantity=4, box_no='FG-PART-2')
        self.assertEqual(first.status, DocumentStatus.APPROVED)
        self.assertEqual(second.status, DocumentStatus.APPROVED)
        line = first.boxes.get().lines.get()
        self.assertEqual((line.ticket_id, line.color, line.size, line.quantity),
                         (self.ticket.pk, '黑', 'M', 6))
        balance = get_balance(self.company, self.packing, self.ticket)
        self.assertEqual((balance['received'], balance['qualified'], balance['available']),
                         (10, 10, 10))
        self.assertEqual(get_inventory(self.company), [])
        self.assertEqual(QuantityLedger.objects.filter(event_type='qualified').count(), 2)

    def test_packing_over_quantity_is_rejected_without_side_effects(self):
        self.receive()
        before = QuantityLedger.objects.count()
        with self.assertRaises(ValidationError):
            self.make_task(quantity=11, approve=False)
        self.assertEqual(PackingTask.objects.count(), 0)
        self.assertEqual(PackingBox.objects.count(), 0)
        self.assertEqual(QuantityLedger.objects.count(), before)

    def test_packing_create_and_double_approve_are_idempotent(self):
        self.receive()
        request_id = rid()
        payload = self.packing_payload(quantity=7, box_no='FG-IDEMPOTENT')
        first = create_packing_task(self.company, request_id=request_id, operator=self.user, **payload)
        second = create_packing_task(self.company, request_id=request_id, operator=self.user, **payload)
        self.assertEqual(first.pk, second.pk)
        with self.assertRaises(IdempotencyError):
            create_packing_task(self.company, request_id=request_id, operator=self.user,
                                **self.packing_payload(quantity=6, box_no='FG-IDEMPOTENT'))
        approval_id = rid()
        approve_packing_task(self.company, first.pk, request_id=approval_id, operator=self.user)
        before = QuantityLedger.objects.count()
        approved_again = approve_packing_task(self.company, first.pk, request_id=approval_id,
                                              operator=self.user)
        self.assertEqual(approved_again.status, DocumentStatus.APPROVED)
        with self.assertRaises(IdempotencyError):
            approve_packing_task(self.company, first.pk, request_id=rid(), operator=self.user)
        self.assertEqual(QuantityLedger.objects.count(), before)
        self.assertEqual(PackingTask.objects.count(), 1)

    def test_box_number_company_unique_and_approved_details_frozen(self):
        self.receive()
        task = self.make_task(quantity=4, box_no='FG-FROZEN')
        box = task.boxes.get()
        line = box.lines.get()
        box.box_no = 'FG-RENAMED'
        with self.assertRaises(FrozenError):
            box.save()
        line.quantity = 3
        with self.assertRaises(FrozenError):
            line.save()
        with self.assertRaises(FrozenError):
            line.delete()
        with self.assertRaises(FrozenError):
            PackingBoxLine.objects.create(company=self.company, box=box, ticket=self.ticket2,
                                          quantity=1)
        self.assertEqual(PackingBoxLine.objects.get(pk=line.pk).quantity, 4)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_task(quantity=1, box_no='FG-FROZEN', approve=False)

    def test_packing_approval_failure_rolls_back_all_milestones(self):
        self.receive(quantity=10)
        self.receive(ticket=self.ticket2, quantity=6)
        task = create_packing_task(
            self.company, route_id=self.route.pk, request_id=rid(), operator=self.user,
            boxes=[{'box_no': 'FG-ROLLBACK', 'items': [
                {'ticket_id': self.ticket.pk, 'quantity': 4},
                {'ticket_id': self.ticket2.pk, 'quantity': 3}]}])
        from apps.finished_goods import services
        real_complete = services.complete_stage
        calls = {'count': 0}

        def fail_second(*args, **kwargs):
            calls['count'] += 1
            if calls['count'] == 2:
                raise QuantityError('模拟第二张菲票失败')
            return real_complete(*args, **kwargs)

        before = QuantityLedger.objects.count()
        with patch.object(services, 'complete_stage', side_effect=fail_second):
            with self.assertRaises(QuantityError):
                approve_packing_task(self.company, task.pk, request_id=rid(), operator=self.user)
        task.refresh_from_db()
        self.assertEqual(task.status, DocumentStatus.DRAFT)
        self.assertEqual(QuantityLedger.objects.count(), before)
        self.assertEqual(get_balance(self.company, self.packing, self.ticket)['qualified'], 0)

    def test_partial_inbound_reconciles_box_and_transfers_correct_nodes(self):
        self.receive()
        task = self.make_task(quantity=6)
        first = self.make_inbound(task, quantity=2, warehouse='测试仓', location='TEST-A')
        second = self.make_inbound(task, quantity=4)
        self.assertEqual((first.warehouse_name, first.location_name), ('测试仓', 'TEST-A'))
        inbound_lines = FinishedInboundLine.objects.filter(document__in=[first, second])
        self.assertEqual(sum(row.quantity for row in inbound_lines), 6)
        self.assertTrue(all(row.color == '黑' and row.size == 'M' for row in inbound_lines))
        pack_balance = get_balance(self.company, self.packing, self.ticket)
        in_balance = get_balance(self.company, self.inbound, self.ticket)
        self.assertEqual((pack_balance['transferred'], pack_balance['available']), (6, 4))
        self.assertEqual((in_balance['received'], in_balance['available']), (6, 6))
        pairs = QuantityLedger.objects.filter(request_id__startswith='fgi:')
        self.assertEqual(pairs.count(), 4)
        self.assertEqual({row.node_id for row in pairs}, {self.packing.pk, self.inbound.pk})

    def test_inbound_over_quantity_and_box_ticket_mismatch_are_rejected(self):
        self.receive()
        task = self.make_task(quantity=5)
        self.make_inbound(task, quantity=3)
        box = task.boxes.get()
        with self.assertRaises(ValidationError):
            create_finished_inbound(
                self.company, route_id=self.route.pk,
                lines=[{'box_id': box.pk, 'ticket_id': self.ticket.pk, 'quantity': 3}],
                request_id=rid())
        with self.assertRaises(ValidationError):
            create_finished_inbound(
                self.company, route_id=self.route.pk,
                lines=[{'box_id': box.pk, 'ticket_id': self.ticket2.pk, 'quantity': 1}],
                request_id=rid())
        self.assertEqual(FinishedInbound.objects.count(), 1)
        self.assertEqual(get_balance(self.company, self.inbound, self.ticket)['available'], 3)

    def test_double_inbound_approval_does_not_duplicate_stock_or_ledger(self):
        self.receive()
        task = self.make_task(quantity=5)
        document = self.make_inbound(task, quantity=5)
        before = QuantityLedger.objects.count()
        again = approve_finished_inbound(self.company, document.pk,
                                         request_id=document.approval_request_id,
                                         operator=self.user)
        self.assertEqual(again.status, DocumentStatus.APPROVED)
        with self.assertRaises(IdempotencyError):
            approve_finished_inbound(self.company, document.pk, request_id=rid(),
                                     operator=self.user)
        self.assertEqual(QuantityLedger.objects.count(), before)
        inventory = get_inventory(self.company)
        self.assertEqual(len(inventory), 1)
        self.assertEqual(inventory[0]['available'], 5)

    def test_inbound_approval_failure_rolls_back_document_and_pairs(self):
        self.receive(quantity=10)
        self.receive(ticket=self.ticket2, quantity=6)
        first = self.make_task(quantity=4, box_no='FG-IN-ROLLBACK-1')
        second = create_packing_task(
            self.company, route_id=self.route.pk, request_id=rid(), operator=self.user,
            boxes=[{'box_no': 'FG-IN-ROLLBACK-2',
                    'items': [{'ticket_id': self.ticket2.pk, 'quantity': 3}]}])
        approve_packing_task(self.company, second.pk, request_id=rid())
        document = create_finished_inbound(
            self.company, route_id=self.route.pk, request_id=rid(), lines=[
                {'box_id': first.boxes.get().pk, 'ticket_id': self.ticket.pk, 'quantity': 4},
                {'box_id': second.boxes.get().pk, 'ticket_id': self.ticket2.pk, 'quantity': 3}])
        from apps.finished_goods import services
        real_transfer = services.transfer_stage
        calls = {'count': 0}

        def fail_second(*args, **kwargs):
            calls['count'] += 1
            if calls['count'] == 2:
                raise QuantityError('模拟第二笔入库失败')
            return real_transfer(*args, **kwargs)

        before = QuantityLedger.objects.count()
        with patch.object(services, 'transfer_stage', side_effect=fail_second):
            with self.assertRaises(QuantityError):
                approve_finished_inbound(self.company, document.pk, request_id=rid())
        document.refresh_from_db()
        self.assertEqual(document.status, DocumentStatus.DRAFT)
        self.assertEqual(QuantityLedger.objects.count(), before)
        self.assertEqual(get_balance(self.company, self.inbound, self.ticket)['available'], 0)

    def test_draft_inbound_reserves_box_quantity_without_inflating_stock(self):
        self.receive()
        task = self.make_task(quantity=5)
        box = task.boxes.get()
        kwargs = {'route_id': self.route.pk,
                  'lines': [{'box_id': box.pk, 'ticket_id': self.ticket.pk, 'quantity': 5}]}
        first = create_finished_inbound(self.company, request_id=rid(), **kwargs)
        with self.assertRaises(ValidationError):
            create_finished_inbound(self.company, request_id=rid(), **kwargs)
        self.assertEqual(get_inventory(self.company), [])
        approve_finished_inbound(self.company, first.pk, request_id=rid())
        self.assertEqual(QuantityLedger.objects.filter(
            source=f'finished_goods.inbound:{first.document_no}').count(), 2)
        self.assertEqual(get_inventory(self.company)[0]['available'], 5)
        with self.assertRaises(ValidationError):
            create_finished_inbound(self.company, request_id=rid(), **kwargs)

    def test_inventory_uses_only_net_finished_inbound_available(self):
        self.receive()
        task = self.make_task(quantity=8)
        self.make_inbound(task, quantity=3)
        self.make_inbound(task, quantity=2)
        rows = get_inventory(self.company)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['node_id'], self.inbound.pk)
        self.assertEqual(rows[0]['available'], 5)
        incoming = QuantityLedger.objects.get(company=self.company, node=self.inbound,
                                              event_type='received', quantity=3)
        reverse_quantity(self.company, incoming, request_id=rid())
        rows = get_inventory(self.company)
        self.assertEqual(rows[0]['available'], 2)
        self.assertEqual(rows[0]['reversals'], 1)

    def test_cross_company_objects_are_hidden_and_box_numbers_are_scoped(self):
        self.receive()
        task = self.make_task(quantity=2, box_no='FG-SAME-NO')
        with self.assertRaises(NotFound):
            create_packing_task(self.other_company, request_id=rid(),
                                **self.packing_payload(quantity=1, box_no='FG-X'))
        with self.assertRaises(NotFound):
            approve_packing_task(self.other_company, task.pk, request_id=rid())
        with self.assertRaises(NotFound):
            approve_finished_inbound(self.company, 999999, request_id=rid())
        receive_stage(self.other_company, self.other_packing, self.other_ticket, 5,
                      request_id=rid())
        other_task = create_packing_task(
            self.other_company, route_id=self.other_route.pk, request_id=rid(),
            boxes=[{'box_no': 'FG-SAME-NO',
                    'items': [{'ticket_id': self.other_ticket.pk, 'quantity': 2}]}])
        self.assertEqual(PackingBox.objects.filter(box_no='FG-SAME-NO').count(), 2)
        self.assertNotEqual(other_task.company_id, self.company.pk)

    def test_cross_route_box_cannot_be_inbounded(self):
        self.receive()
        task = self.make_task(quantity=3)
        other_bed = Production.objects.create(company=self.company, style=self.style,
                                              bed_no='FG-B2', total_qty=5)
        other_ticket = Ticket.objects.create(company=self.company, production=other_bed,
                                             bed_no=other_bed.bed_no, bundle_no='1', qty=5,
                                             qr_code='FG-B2-1')
        other_route = ProductionRoute.objects.create(company=self.company, code='FG-R2',
                                                     production=other_bed)
        RouteNode.objects.create(company=self.company, route=other_route,
                                 stage=self.packing.stage, sequence=1)
        RouteNode.objects.create(company=self.company, route=other_route,
                                 stage=self.inbound.stage, sequence=2)
        with self.assertRaises(ValidationError):
            create_finished_inbound(
                self.company, route_id=other_route.pk,
                lines=[{'box_id': task.boxes.get().pk, 'ticket_id': other_ticket.pk,
                        'quantity': 1}], request_id=rid())
        self.assertEqual(FinishedInbound.objects.count(), 0)

    def test_route_requires_exact_forward_packing_and_inbound_nodes(self):
        bed = Production.objects.create(company=self.company, style=self.style,
                                        bed_no='FG-BAD-NODES', total_qty=2)
        ticket = Ticket.objects.create(company=self.company, production=bed, bed_no=bed.bed_no,
                                       bundle_no='1', qty=2, qr_code='FG-BAD-NODES-1')
        route = ProductionRoute.objects.create(company=self.company, code='FG-BAD-R',
                                               production=bed)
        RouteNode.objects.create(company=self.company, route=route, stage=self.inbound.stage,
                                 sequence=1)
        RouteNode.objects.create(company=self.company, route=route, stage=self.packing.stage,
                                 sequence=2)
        with self.assertRaises(ValidationError):
            create_packing_task(
                self.company, route_id=route.pk, request_id=rid(),
                boxes=[{'box_no': 'FG-BAD-NODE-BOX',
                        'items': [{'ticket_id': ticket.pk, 'quantity': 1}]}])
        self.assertFalse(PackingTask.objects.filter(route=route).exists())

    def test_inbound_create_idempotency_and_payload_conflict(self):
        self.receive()
        task = self.make_task(quantity=4)
        box = task.boxes.get()
        request_id = rid()
        kwargs = {'route_id': self.route.pk,
                  'lines': [{'box_id': box.pk, 'ticket_id': self.ticket.pk, 'quantity': 2}]}
        first = create_finished_inbound(self.company, request_id=request_id, **kwargs)
        second = create_finished_inbound(self.company, request_id=request_id, **kwargs)
        self.assertEqual(first.pk, second.pk)
        kwargs['lines'][0]['quantity'] = 1
        with self.assertRaises(IdempotencyError):
            create_finished_inbound(self.company, request_id=request_id, **kwargs)
        self.assertEqual(FinishedInbound.objects.count(), 1)

    def test_approved_inbound_lines_are_frozen(self):
        self.receive()
        task = self.make_task(quantity=3)
        document = self.make_inbound(task, quantity=3)
        line = document.lines.get()
        line.quantity = 1
        with self.assertRaises(FrozenError):
            line.save()
        with self.assertRaises(FrozenError):
            line.delete()
        self.assertEqual(FinishedInboundLine.objects.get(pk=line.pk).quantity, 3)

    def test_inbound_then_packing_reversal_restores_quantities_and_is_idempotent(self):
        self.receive()
        task = self.make_task(quantity=5)
        document = self.make_inbound(task, quantity=5)
        request_id = rid()
        reverse_finished_inbound(self.company, document.pk, request_id=request_id,
                                 reason='入库单录入错误', operator=self.user)
        before = QuantityLedger.objects.count()
        again = reverse_finished_inbound(self.company, document.pk, request_id=request_id,
                                         reason='入库单录入错误', operator=self.user)
        self.assertEqual(again.status, DocumentStatus.REVERSED)
        self.assertEqual(QuantityLedger.objects.count(), before)
        self.assertEqual(get_inventory(self.company), [])
        self.assertEqual(get_balance(self.company, self.packing, self.ticket)['available'], 10)
        reverse_packing_task(self.company, task.pk, request_id=rid(),
                             reason='包装明细错误', operator=self.user)
        task.refresh_from_db()
        self.assertEqual(task.status, DocumentStatus.REVERSED)
        self.assertEqual(get_balance(self.company, self.packing, self.ticket)['qualified'], 0)
        self.assertTrue(QuantityLedger.objects.filter(reversal_of__isnull=False).exists())

    def test_packing_reversal_is_blocked_by_draft_or_approved_inbound(self):
        self.receive()
        task = self.make_task(quantity=5)
        document = self.make_inbound(task, quantity=2, approve=False)
        with self.assertRaises(ValidationError):
            reverse_packing_task(self.company, task.pk, request_id=rid(), reason='错误')
        reverse_finished_inbound(self.company, document.pk, request_id=rid(), reason='取消草稿')
        reverse_packing_task(self.company, task.pk, request_id=rid(), reason='包装错误')
        self.assertEqual(get_balance(self.company, self.packing, self.ticket)['qualified'], 0)

    def test_inbound_reversal_is_blocked_after_finished_stock_shipment(self):
        self.receive()
        task = self.make_task(quantity=5)
        document = self.make_inbound(task, quantity=5)
        release_finished_stock(self.company, self.inbound, self.ticket, 1, operator=self.user,
                               request_id=rid(), source='shipment:999')
        before = QuantityLedger.objects.count()
        with self.assertRaises(ValidationError):
            reverse_finished_inbound(self.company, document.pk, request_id=rid(), reason='错误')
        document.refresh_from_db()
        self.assertEqual(document.status, DocumentStatus.APPROVED)
        self.assertEqual(QuantityLedger.objects.count(), before)
        self.assertEqual(get_inventory(self.company)[0]['available'], 4)

    def test_draft_reversal_does_not_write_ledger_or_allow_reapproval(self):
        self.receive()
        task = self.make_task(quantity=5)
        document = self.make_inbound(task, quantity=5, approve=False)
        before = QuantityLedger.objects.count()
        reverse_finished_inbound(self.company, document.pk, request_id=rid(), reason='取消录入')
        self.assertEqual(QuantityLedger.objects.count(), before)
        with self.assertRaises(ValidationError):
            approve_finished_inbound(self.company, document.pk, request_id=rid())
        with self.assertRaises(IdempotencyError):
            reverse_finished_inbound(self.company, document.pk, request_id=rid(), reason='不同原因')
