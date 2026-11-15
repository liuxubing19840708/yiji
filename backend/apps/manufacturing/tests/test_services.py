"""运行：DJANGO_SETTINGS_MODULE=apps.manufacturing.tests.test_settings python manage.py test apps.manufacturing"""
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.accounts.models import Company, User
from apps.catalog.models import Style, Ticket
from apps.manufacturing.exceptions import (IdempotencyError, ImmutableLedgerError, NotFound,
                                           QuantityError, TransitionError, ValidationError)
from apps.manufacturing.models import (ProductionRoute, ProductionStage, QuantityLedger,
                                       RouteNode, StageExecution)
from apps.orders.models import Order
from apps.manufacturing.services import (assert_can_transition, complete_stage, get_balance,
                                         get_ticket_node_balances, instantiate_route, post_quantity,
                                         receive_stage, return_stage, reverse_quantity, scrap_stage,
                                         transfer_stage)
from apps.production.models import Production


def rid():
    return uuid4().hex


class ManufacturingServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='mfg-owner', phone='13900000001',
                                            password='password')
        cls.company = Company.objects.create(name='制造一厂', company_no='900001',
                                             creator=cls.user)
        cls.other_company = Company.objects.create(name='制造二厂', company_no='900002',
                                                   creator=cls.user)
        cls.style = Style.objects.create(company=cls.company, style_no='MFG-S1', name='测试款')
        cls.bed = Production.objects.create(company=cls.company, style=cls.style,
                                            bed_no='MFG-B1', total_qty=10)
        cls.ticket = Ticket.objects.create(company=cls.company, production=cls.bed,
                                           bed_no=cls.bed.bed_no, bundle_no='1', color='黑',
                                           size='M', qty=10, qr_code='MFG-B1-1')
        cls.template = ProductionRoute.objects.create(company=cls.company, code='TEMPLATE',
                                                      name='标准路线', is_template=True)
        specs = [
            ('cut', '裁剪', ProductionStage.StageType.CUTTING, 1, True, False, False),
            ('sew', '车缝', ProductionStage.StageType.SEWING, 2, True, False, False),
            ('special', '专机', ProductionStage.StageType.SPECIAL_MACHINE, 3, True, True, False),
            ('iron', '整烫', ProductionStage.StageType.IRONING, 4, True, True, False),
            ('final', '终检', ProductionStage.StageType.FINAL_INSPECTION, 5, True, False, False),
            ('pack', '包装', ProductionStage.StageType.PACKING, 6, False, False, True),
            ('inbound', '入库', ProductionStage.StageType.FINISHED_INBOUND, 7, True, False, False),
        ]
        for code, name, stage_type, sequence, required, parallel, skip in specs:
            stage = ProductionStage.objects.create(company=cls.company, code=code, name=name,
                                                   stage_type=stage_type)
            RouteNode.objects.create(company=cls.company, route=cls.template, stage=stage,
                                     sequence=sequence, required=required,
                                     allow_parallel=parallel, allow_skip=skip)
        cls.route = instantiate_route(cls.template, company=cls.company, production=cls.bed,
                                      code='ROUTE-B1')
        cls.nodes = {node.stage.code: node for node in cls.route.nodes.select_related('stage')}

    def cut_in(self, quantity=10):
        return receive_stage(self.company, self.nodes['cut'], self.ticket, quantity,
                             operator=self.user, request_id=rid())

    def qualify_cut(self, quantity=10):
        return complete_stage(self.company, self.nodes['cut'], self.ticket, quantity,
                              operator=self.user, request_id=rid())

    def to_sewing(self, quantity=10):
        self.cut_in(quantity)
        self.qualify_cut(quantity)
        return transfer_stage(self.company, self.nodes['cut'], self.nodes['sew'], self.ticket,
                              quantity, operator=self.user, request_id=rid())

    def test_route_instantiation_and_normal_flow_formula(self):
        self.assertEqual(self.route.nodes.count(), 7)
        self.assertEqual(self.route.source_template, self.template)
        self.assertFalse(self.route.is_template)
        self.to_sewing()
        cut = get_balance(self.company, self.nodes['cut'], self.ticket)
        sew = get_balance(self.company, self.nodes['sew'], self.ticket)
        self.assertEqual(cut['received'], 10)
        self.assertEqual(cut['qualified'], 10)
        self.assertEqual(cut['transferred'], 10)
        self.assertEqual(cut['available'], 0)  # 不是 received-qualified-transferred = -10
        self.assertEqual(cut['transferable'], 0)
        self.assertEqual(sew['received'], 10)
        self.assertEqual(sew['available'], 10)
        self.assertEqual(StageExecution.objects.filter(company=self.company).count(), 4)
        self.assertEqual(QuantityLedger.objects.filter(company=self.company).count(), 4)
        # 多工序生产量只读指定节点合格数，不对全路线求和。
        self.assertNotEqual(cut['qualified'] + sew['received'], self.ticket.qty)

    def test_positive_and_over_quantity_guards(self):
        for invalid in (0, -1, True, 1.5, '10'):
            with self.subTest(invalid=invalid), self.assertRaises(QuantityError):
                post_quantity(self.company, self.nodes['cut'], self.ticket,
                              QuantityLedger.Event.RECEIVED, invalid, request_id=rid())
        self.cut_in()
        with self.assertRaises(QuantityError):
            self.cut_in(1)
        with self.assertRaises(QuantityError):
            self.qualify_cut(11)
        self.qualify_cut(8)
        with self.assertRaises(TransitionError):
            transfer_stage(self.company, self.nodes['cut'], self.nodes['sew'], self.ticket, 9,
                           request_id=rid())
        self.assertEqual(get_balance(self.company, self.nodes['cut'], self.ticket)['available'], 10)

    def test_required_cannot_skip_but_explicit_skip_can(self):
        self.cut_in()
        self.qualify_cut()
        with self.assertRaises(TransitionError):
            transfer_stage(self.company, self.nodes['cut'], self.nodes['special'], self.ticket, 10,
                           request_id=rid())
        sew = self.nodes['sew']
        sew.allow_skip = True
        sew.save(update_fields=['allow_skip', 'updated_at'])
        transfer_stage(self.company, self.nodes['cut'], self.nodes['special'], self.ticket, 10,
                       request_id=rid())
        self.assertEqual(get_balance(self.company, self.nodes['special'], self.ticket)['received'], 10)

    def test_parallel_group_and_required_exit_gate(self):
        self.to_sewing()
        complete_stage(self.company, self.nodes['sew'], self.ticket, 10, request_id=rid())
        transfer_stage(self.company, self.nodes['sew'], self.nodes['special'], self.ticket, 6,
                       request_id=rid())
        transfer_stage(self.company, self.nodes['sew'], self.nodes['iron'], self.ticket, 4,
                       request_id=rid())
        complete_stage(self.company, self.nodes['special'], self.ticket, 6, request_id=rid())
        with self.assertRaises(TransitionError):
            transfer_stage(self.company, self.nodes['special'], self.nodes['final'], self.ticket, 6,
                           request_id=rid())
        complete_stage(self.company, self.nodes['iron'], self.ticket, 4, request_id=rid())
        transfer_stage(self.company, self.nodes['iron'], self.nodes['final'], self.ticket, 4,
                       request_id=rid())
        self.assertEqual(get_balance(self.company, self.nodes['final'], self.ticket)['available'], 4)

    def test_rework_loop_returns_unqualified_and_can_flow_again(self):
        self.to_sewing()
        outgoing, incoming = return_stage(self.company, self.nodes['sew'], self.nodes['cut'],
                                          self.ticket, 4, operator=self.user, request_id=rid(),
                                          reason='线头返修')
        self.assertEqual(outgoing.event_type, QuantityLedger.Event.TRANSFERRED)
        self.assertEqual(incoming.event_type, QuantityLedger.Event.REWORK)
        self.assertEqual(incoming.related_entry, outgoing)
        self.assertEqual(outgoing.execution.action, StageExecution.Action.RETURN)
        self.assertEqual(get_balance(self.company, self.nodes['cut'], self.ticket)['available'], 4)
        self.assertEqual(get_balance(self.company, self.nodes['sew'], self.ticket)['available'], 6)
        complete_stage(self.company, self.nodes['cut'], self.ticket, 4, request_id=rid())
        transfer_stage(self.company, self.nodes['cut'], self.nodes['sew'], self.ticket, 4,
                       request_id=rid())
        self.assertEqual(get_balance(self.company, self.nodes['sew'], self.ticket)['available'], 10)

    def test_partial_rework_does_not_consume_forward_transferable_quantity(self):
        self.to_sewing()
        complete_stage(self.company, self.nodes['sew'], self.ticket, 9, request_id=rid())
        return_stage(self.company, self.nodes['sew'], self.nodes['cut'], self.ticket, 1,
                     request_id=rid(), reason='一件返修')
        balance = get_balance(self.company, self.nodes['sew'], self.ticket)
        self.assertEqual((balance['available'], balance['transferable'],
                          balance['qualification_remaining']), (9, 9, 0))
        transfer_stage(self.company, self.nodes['sew'], self.nodes['special'], self.ticket, 9,
                       request_id=rid())
        complete_stage(self.company, self.nodes['cut'], self.ticket, 1, request_id=rid())
        transfer_stage(self.company, self.nodes['cut'], self.nodes['sew'], self.ticket, 1,
                       request_id=rid())
        complete_stage(self.company, self.nodes['sew'], self.ticket, 1, request_id=rid())
        balance = get_balance(self.company, self.nodes['sew'], self.ticket)
        self.assertEqual((balance['available'], balance['transferable'],
                          balance['qualification_remaining']), (1, 1, 0))
        with self.assertRaises(QuantityError):
            complete_stage(self.company, self.nodes['sew'], self.ticket, 1, request_id=rid())

    def test_scrap_and_its_reversal(self):
        self.cut_in()
        scrapped = scrap_stage(self.company, self.nodes['cut'], self.ticket, 3,
                               operator=self.user, request_id=rid())
        balance = get_balance(self.company, self.nodes['cut'], self.ticket)
        self.assertEqual((balance['available'], balance['qualification_remaining']), (7, 7))
        complete_stage(self.company, self.nodes['cut'], self.ticket, 7, request_id=rid())
        with self.assertRaises(QuantityError):
            scrap_stage(self.company, self.nodes['cut'], self.ticket, 1, request_id=rid())
        reverse_quantity(self.company, scrapped, request_id=rid())
        balance = get_balance(self.company, self.nodes['cut'], self.ticket)
        self.assertEqual((balance['scrapped'], balance['available']), (0, 10))
        self.assertEqual(balance['reversals'], 1)

    def test_reversal_is_immutable_append_only_and_pair_order_is_enforced(self):
        received = self.cut_in()
        reversal = reverse_quantity(self.company, received, operator=self.user, request_id=rid())
        self.assertEqual(reversal.reversal_of, received)
        self.assertEqual(get_balance(self.company, self.nodes['cut'], self.ticket)['available'], 0)
        self.assertEqual(QuantityLedger.objects.get(pk=received.pk).quantity, 10)
        with self.assertRaises(ImmutableLedgerError):
            received.quantity = 8
            received.save()
        with self.assertRaises(ImmutableLedgerError):
            received.delete()
        with self.assertRaises(ValidationError):
            reverse_quantity(self.company, received, request_id=rid())

        # 新起一轮正常配对，验证接收端先冲销，原转出端后冲销。
        self.cut_in()
        self.qualify_cut()
        outgoing, incoming = transfer_stage(self.company, self.nodes['cut'], self.nodes['sew'],
                                            self.ticket, 10, request_id=rid())
        with self.assertRaises(QuantityError):
            reverse_quantity(self.company, outgoing, request_id=rid())
        reverse_quantity(self.company, incoming, request_id=rid())
        reverse_quantity(self.company, outgoing, request_id=rid())
        self.assertEqual(get_balance(self.company, self.nodes['cut'], self.ticket)['available'], 10)
        self.assertEqual(get_balance(self.company, self.nodes['sew'], self.ticket)['available'], 0)

    def test_idempotency_and_payload_conflict(self):
        request_id = rid()
        first = receive_stage(self.company, self.nodes['cut'], self.ticket, 10,
                              operator=self.user, request_id=request_id)
        second = receive_stage(self.company, self.nodes['cut'], self.ticket, 10,
                               operator=self.user, request_id=request_id)
        self.assertEqual(first, second)
        self.assertEqual(QuantityLedger.objects.count(), 1)
        with self.assertRaises(IdempotencyError):
            receive_stage(self.company, self.nodes['cut'], self.ticket, 9,
                          operator=self.user, request_id=request_id)
        self.qualify_cut()
        transfer_id = rid()
        pair1 = transfer_stage(self.company, self.nodes['cut'], self.nodes['sew'], self.ticket, 10,
                               operator=self.user, request_id=transfer_id)
        pair2 = transfer_stage(self.company, self.nodes['cut'], self.nodes['sew'], self.ticket, 10,
                               operator=self.user, request_id=transfer_id)
        self.assertEqual([x.pk for x in pair1], [x.pk for x in pair2])
        self.assertEqual(QuantityLedger.objects.count(), 4)
        reverse_id = rid()
        rev1 = reverse_quantity(self.company, pair1[1], request_id=reverse_id)
        rev2 = reverse_quantity(self.company, pair1[1], request_id=reverse_id)
        self.assertEqual(rev1, rev2)
        self.assertEqual(QuantityLedger.objects.count(), 5)

    def test_company_isolation_for_read_write_and_reverse(self):
        entry = self.cut_in()
        with self.assertRaises(NotFound):
            get_balance(self.other_company, self.nodes['cut'], self.ticket)
        with self.assertRaises(NotFound):
            post_quantity(self.other_company, self.nodes['cut'], self.ticket,
                          QuantityLedger.Event.RECEIVED, 1, request_id=rid())
        with self.assertRaises(NotFound):
            reverse_quantity(self.other_company, entry, request_id=rid())
        # 不同公司可安全复用同一 request_id，但上下文仍严格隔离。
        self.assertEqual(QuantityLedger.objects.filter(company=self.other_company).count(), 0)

    def test_ticket_node_balance_view_includes_zero_rows(self):
        Ticket.objects.create(company=self.company, production=self.bed, bed_no=self.bed.bed_no,
                              bundle_no='2', color='白', size='L', qty=5, qr_code='MFG-B1-2')
        self.cut_in()
        rows = get_ticket_node_balances(self.company, self.route)
        self.assertEqual(len(rows), 14)
        zero = [row for row in rows if row['bundle_no'] == '2']
        self.assertTrue(all(row['balance']['available'] == 0 for row in zero))
        cut_row = next(row for row in rows if row['bundle_no'] == '1'
                       and row['stage_type'] == ProductionStage.StageType.CUTTING)
        self.assertEqual(cut_row['balance']['available'], 10)

    def test_template_cannot_post_and_wrong_bed_is_rejected(self):
        template_node = self.template.nodes.order_by('sequence').first()
        with self.assertRaises(ValidationError):
            post_quantity(self.company, template_node, self.ticket,
                          QuantityLedger.Event.RECEIVED, 1, request_id=rid())
        other_bed = Production.objects.create(company=self.company, style=self.style,
                                              bed_no='MFG-B2', total_qty=5)
        other_ticket = Ticket.objects.create(company=self.company, production=other_bed,
                                             bed_no=other_bed.bed_no, bundle_no='1', qty=5,
                                             qr_code='MFG-B2-1')
        with self.assertRaises(ValidationError):
            receive_stage(self.company, self.nodes['cut'], other_ticket, 1, request_id=rid())

    def test_database_positive_and_request_uniqueness_constraints(self):
        entry = self.cut_in()
        with self.assertRaises(IntegrityError), transaction.atomic():
            QuantityLedger.objects.create(
                company=self.company, route=self.route, node=self.nodes['cut'],
                production=self.bed, ticket=self.ticket, event_type=QuantityLedger.Event.RECEIVED,
                quantity=0, request_id=rid(), request_fingerprint='0' * 64)
        with self.assertRaises(IntegrityError), transaction.atomic():
            QuantityLedger.objects.create(
                company=self.company, route=self.route, node=self.nodes['cut'],
                production=self.bed, ticket=self.ticket, event_type=QuantityLedger.Event.RECEIVED,
                quantity=1, request_id=entry.request_id, request_fingerprint='1' * 64)

    def test_assert_can_transition_rejects_backward_without_rework(self):
        self.to_sewing()
        with self.assertRaises(TransitionError):
            assert_can_transition(self.company, self.nodes['sew'], self.nodes['cut'], self.ticket, 1)
        self.assertTrue(assert_can_transition(
            self.company, self.nodes['sew'], self.nodes['cut'], self.ticket, 1,
            allow_rework=True))

    def test_supplement_cut_cannot_exceed_ticket_route_stock(self):
        self.to_sewing()
        before = QuantityLedger.objects.count()
        # 原 10 件已在车缝，首节点不能凭空调出另外 10 件。
        with self.assertRaises(QuantityError):
            receive_stage(self.company, self.nodes['cut'], self.ticket, 10, request_id=rid())
        self.assertEqual(QuantityLedger.objects.count(), before)
        self.assertEqual(get_balance(self.company, self.nodes['cut'], self.ticket)['available'], 0)
        # 报废 2 件释放补裁额度，补接 2 件后全路线在手仍为 10。
        scrap_stage(self.company, self.nodes['sew'], self.ticket, 2, request_id=rid())
        receive_stage(self.company, self.nodes['cut'], self.ticket, 2, request_id=rid())
        self.assertEqual(get_balance(self.company, self.nodes['cut'], self.ticket)['available'], 2)
        self.assertEqual(get_balance(self.company, self.nodes['sew'], self.ticket)['available'], 8)

    def test_scrap_reversal_after_replacement_cannot_overfill_route(self):
        self.cut_in()
        scrapped = scrap_stage(self.company, self.nodes['cut'], self.ticket, 10, request_id=rid())
        receive_stage(self.company, self.nodes['cut'], self.ticket, 10, request_id=rid())
        with self.assertRaises(QuantityError):
            reverse_quantity(self.company, scrapped, request_id=rid())
        self.assertEqual(get_balance(self.company, self.nodes['cut'], self.ticket)['available'], 10)

    def test_pair_request_id_must_be_nonempty_and_leave_suffix_space(self):
        self.cut_in()
        self.qualify_cut()
        for invalid in ('', '   ', None, 'x' * 61):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                transfer_stage(self.company, self.nodes['cut'], self.nodes['sew'], self.ticket, 1,
                               request_id=invalid)
        self.assertEqual(QuantityLedger.objects.count(), 2)

    def test_order_route_supports_tickets_from_its_beds(self):
        order = Order.objects.create(company=self.company, order_no='MFG-O1', style=self.style)
        bed = Production.objects.create(company=self.company, style=self.style, order=order,
                                        bed_no='MFG-ORDER-BED', total_qty=5)
        ticket = Ticket.objects.create(company=self.company, production=bed, bed_no=bed.bed_no,
                                       bundle_no='1', qty=5, qr_code='MFG-ORDER-BED-1')
        route = instantiate_route(self.template, company=self.company, order=order,
                                  code='MFG-ORDER-ROUTE')
        first = route.nodes.order_by('sequence').first()
        entry = receive_stage(self.company, first, ticket, 5, request_id=rid())
        self.assertEqual(entry.order, order)
        self.assertEqual(entry.production, bed)
        self.assertEqual(get_balance(self.company, first, ticket)['available'], 5)

    def test_return_retry_is_idempotent_and_cannot_move_forward(self):
        self.to_sewing()
        request_id = rid()
        first = return_stage(self.company, self.nodes['sew'], self.nodes['cut'], self.ticket, 4,
                             request_id=request_id)
        second = return_stage(self.company, self.nodes['sew'], self.nodes['cut'], self.ticket, 4,
                              request_id=request_id)
        self.assertEqual([row.pk for row in first], [row.pk for row in second])
        self.assertEqual(QuantityLedger.objects.count(), 6)
        with self.assertRaises(TransitionError):
            return_stage(self.company, self.nodes['cut'], self.nodes['sew'], self.ticket, 1,
                         request_id=rid())
