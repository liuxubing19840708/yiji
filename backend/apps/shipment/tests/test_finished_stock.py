# -*- coding: utf-8 -*-
"""新生产路线订单：出货只消耗 finished_inbound 现货，作废按反向流水回补。"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.exceptions import ValidationError

from apps.accounts.models import Company, User
from apps.catalog.models import Customer, Style, Ticket
from apps.finished_goods.services import (
    approve_finished_inbound, approve_packing_task, create_finished_inbound,
    create_packing_task, get_inventory,
)
from apps.manufacturing.models import ProductionRoute, ProductionStage, QuantityLedger, RouteNode
from apps.manufacturing.services import receive_stage
from apps.orders.models import Order, OrderItem
from apps.production.models import Production
from apps.shipment.models import PackingBox, Shipment, ShipmentItem
from apps.shipment.services import audit_shipment, produced_qty_map, shipped_qty_map, void_shipment


class FinishedStockShipmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username='shipment-fg', phone='13900000011', password='x')
        cls.company = Company.objects.create(
            name='出货成品流水隔离测试', company_no='990011', creator=cls.user)
        cls.customer = Customer.objects.create(
            company=cls.company, customer_no='C-FG', name='测试客户')
        cls.style = Style.objects.create(company=cls.company, style_no='S-FG', name='流水款')
        cls.order = Order.objects.create(
            company=cls.company, order_no='SO-FG', customer=cls.customer, style=cls.style)
        OrderItem.objects.create(order=cls.order, color='红', size='M', qty=10, price=Decimal('10'))
        cls.bed = Production.objects.create(
            company=cls.company, style=cls.style, order=cls.order, customer=cls.customer,
            bed_no='BED-FG', total_qty=10)
        cls.ticket = Ticket.objects.create(
            company=cls.company, production=cls.bed, bed_no=cls.bed.bed_no,
            bundle_no='1', color='红', size='M', qty=10, qr_code='BED-FG|1')
        cls.route = ProductionRoute.objects.create(
            company=cls.company, code='ROUTE-FG', name='成品路线', order=cls.order)
        packing_stage = ProductionStage.objects.create(
            company=cls.company, code='PACK-FG', name='包装',
            stage_type=ProductionStage.StageType.PACKING)
        inbound_stage = ProductionStage.objects.create(
            company=cls.company, code='IN-FG', name='成品入库',
            stage_type=ProductionStage.StageType.FINISHED_INBOUND)
        cls.packing = RouteNode.objects.create(
            company=cls.company, route=cls.route, stage=packing_stage, sequence=1)
        cls.inbound = RouteNode.objects.create(
            company=cls.company, route=cls.route, stage=inbound_stage, sequence=2)

    def setUp(self):
        receive_stage(self.company, self.packing, self.ticket, 10,
                      operator=self.user, request_id='FG-RECEIVE')
        self.task = create_packing_task(
            self.company, route_id=self.route.pk, request_id='FG-PACK-CREATE',
            operator=self.user, boxes=[{'box_no': 'BOX-FG-1',
                                       'items': [{'ticket_id': self.ticket.pk, 'quantity': 10}]}])
        approve_packing_task(self.company, self.task.pk, request_id='FG-PACK-APPROVE',
                             operator=self.user)
        box = self.task.boxes.get()
        line = box.lines.get()
        self.inbound_doc = create_finished_inbound(
            self.company, route_id=self.route.pk, request_id='FG-IN-CREATE', operator=self.user,
            lines=[{'box_id': box.pk, 'ticket_id': self.ticket.pk, 'quantity': 10}])
        approve_finished_inbound(self.company, self.inbound_doc.pk,
                                 request_id='FG-IN-APPROVE', operator=self.user)
        self.assertEqual(line.quantity, 10)

    def shipment(self, qty, no='SH-FG-1', box_qty=None):
        shipment = Shipment.objects.create(
            company=self.company, order=self.order, customer=self.customer,
            shipment_no=no, total_qty=qty)
        ShipmentItem.objects.create(shipment=shipment, color='红', size='M', qty=qty)
        if box_qty is not None:
            PackingBox.objects.create(shipment=shipment, box_no='BOX-FG-1',
                                      color='红', size='M', qty=box_qty)
        return shipment

    def inventory(self):
        return get_inventory(self.company, order_id=self.order.pk)[0]['available']

    def test_audit_consumes_real_stock_and_void_reverses_it(self):
        self.assertEqual(produced_qty_map(self.company, self.order), {('红', 'M'): 10})
        shipment = self.shipment(6, box_qty=6)
        audit_shipment(self.company, shipment, self.user)
        self.assertEqual(self.inventory(), 4)
        self.assertEqual(shipped_qty_map(self.company, self.order), {('红', 'M'): 6})
        self.assertEqual(QuantityLedger.objects.filter(source=f'shipment:{shipment.pk}').count(), 1)

        over = self.shipment(5, no='SH-FG-OVER', box_qty=5)
        with self.assertRaises(ValidationError):
            audit_shipment(self.company, over, self.user)
        over.refresh_from_db()
        self.assertEqual(over.status, Shipment.Status.DRAFT)
        self.assertEqual(self.inventory(), 4)

        void_shipment(self.company, shipment, self.user)
        self.assertEqual(self.inventory(), 10)
        self.assertEqual(shipped_qty_map(self.company, self.order), {})
        self.assertTrue(QuantityLedger.objects.filter(
            source=f'shipment:{shipment.pk}', event_type=QuantityLedger.Event.REVERSAL).exists())

    def test_missing_or_mismatched_boxes_do_not_consume_stock(self):
        without_box = self.shipment(4, no='SH-FG-NO-BOX')
        with self.assertRaises(ValidationError):
            audit_shipment(self.company, without_box, self.user)
        mismatched = self.shipment(4, no='SH-FG-BAD-BOX', box_qty=3)
        with self.assertRaises(ValidationError):
            audit_shipment(self.company, mismatched, self.user)
        self.assertEqual(self.inventory(), 10)
        self.assertFalse(QuantityLedger.objects.filter(source__startswith='shipment:').exists())
