# -*- coding: utf-8 -*-
"""复杂订单变更门禁：已审核出货是不可突破的财务事实。

已裁/已报工后的生产改单沿用既有可重排合同；已审核出货不得被改单破坏。
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer, Process, Style, Ticket
from apps.finance.models import Receivable
from apps.orders.models import Order, OrderItem
from apps.production.models import Production, ProductionProcess
from apps.records.models import Record
from apps.shipment.models import Shipment, ShipmentItem


class OrderChangeGuardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username='order-change-owner', phone='13900007101', password='test-only')
        cls.company, _ = create_company_with_owner(cls.owner, '复杂改单隔离测试厂')
        cls.customer = Customer.objects.create(
            company=cls.company, customer_no='OC-C1', name='改单客户')
        cls.style = Style.objects.create(
            company=cls.company, style_no='OC-S1', name='改单测试款')
        cls.process = Process.objects.create(
            company=cls.company, name='OC-车缝', default_price='2.500', is_fixed=True)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.client.credentials(HTTP_X_COMPANY_ID=str(self.company.pk))
        self.order = Order.objects.create(
            company=self.company, order_no='OC-ORDER',
            customer=self.customer, style=self.style)
        self.item = OrderItem.objects.create(
            order=self.order, color='黑', size='M', qty=100, price='10.000')

    def _put_qty(self, qty):
        return self.client.put(f'/api/orders/{self.order.pk}', {
            'style': self.style.pk,
            'items': [{'color': '黑', 'size': 'M', 'qty': qty, 'price': '10.000'}],
        }, format='json')

    def _assert_rejected_and_unchanged(self, response, needle):
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn(needle, str(response.data))
        self.item.refresh_from_db()
        self.assertEqual(self.item.qty, 100)
        self.assertEqual(self.item.price, Decimal('10.000'))

    def _production_ticket(self, qty=100):
        production = Production.objects.create(
            company=self.company, order=self.order, style=self.style,
            bed_no='OC-BED', total_qty=qty, plan_qty=qty)
        ProductionProcess.objects.create(
            production=production, process=self.process, price='2.500')
        ticket = Ticket.objects.create(
            company=self.company, production=production, bed_no=production.bed_no,
            bundle_no='1', color='黑', size='M', qty=qty,
            qr_code=f'{self.company.company_no}|{production.bed_no}|OC-S1|1|黑|M|{qty}')
        return production, ticket

    def test_cannot_reduce_below_approved_shipment(self):
        shipment = Shipment.objects.create(
            company=self.company, shipment_no='OC-SHIPPED', order=self.order,
            customer=self.customer, total_qty=60, status=Shipment.Status.PARTIAL)
        ShipmentItem.objects.create(shipment=shipment, color='黑', size='M', qty=60)
        response = self._put_qty(59)
        self._assert_rejected_and_unchanged(response, '已出')

    def test_cut_and_registered_orders_keep_existing_rearrangement_contract(self):
        """已裁/已报工允许改单重排；进度按新分母计算，不能误当财务出货拦截。"""
        self._production_ticket(qty=80)
        response = self._put_qty(79)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.order.items.get().qty, 79)

    def test_registered_progress_uses_new_order_quantity(self):
        _production, ticket = self._production_ticket(qty=100)
        Record.objects.create(
            company=self.company, ticket=ticket, process=self.process,
            user=self.owner, qty=80, price='2.500')
        response = self._put_qty(160)
        self.assertEqual(response.status_code, 200, response.data)
        response = self.client.get(f'/api/orders/{self.order.pk}/progress')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['registered_qty'], 80)
        self.assertEqual(response.data['progress'], 50.0)

    def test_cannot_remove_or_rename_shipped_color_size(self):
        shipment = Shipment.objects.create(
            company=self.company, shipment_no='OC-REMOVE', order=self.order,
            customer=self.customer, total_qty=20, status=Shipment.Status.PARTIAL)
        ShipmentItem.objects.create(shipment=shipment, color='黑', size='M', qty=20)
        response = self.client.put(f'/api/orders/{self.order.pk}', {
            'style': self.style.pk,
            'items': [{'color': '蓝', 'size': 'M', 'qty': 100, 'price': '12.000'}],
        }, format='json')
        self._assert_rejected_and_unchanged(response, '黑/M')
        self.assertEqual(self.order.items.count(), 1)

    def test_price_change_does_not_rewrite_audited_receivable(self):
        """改价只影响后续出货；已审核应收是历史快照，不能随订单改价漂移。"""
        shipment = Shipment.objects.create(
            company=self.company, shipment_no='OC-PRICE', order=self.order,
            customer=self.customer, total_qty=60, status=Shipment.Status.PARTIAL)
        ShipmentItem.objects.create(shipment=shipment, color='黑', size='M', qty=60)
        receivable = Receivable.objects.create(
            company=self.company, receivable_no='OC-AR', customer=self.customer,
            order=self.order, shipment=shipment, amount='600.00')
        response = self.client.put(f'/api/orders/{self.order.pk}', {
            'style': self.style.pk,
            'items': [{'color': '黑', 'size': 'M', 'qty': 100, 'price': '12.000'}],
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.item = self.order.items.get()
        receivable.refresh_from_db()
        self.assertEqual(self.item.price, Decimal('12.000'))
        self.assertEqual(receivable.amount, Decimal('600.00'))

    def test_increase_and_equal_to_downstream_quantity_are_allowed(self):
        _production, ticket = self._production_ticket(qty=80)
        Record.objects.create(
            company=self.company, ticket=ticket, process=self.process,
            user=self.owner, qty=80, price='2.500')
        response = self._put_qty(80)
        self.assertEqual(response.status_code, 200, response.data)
        response = self._put_qty(120)
        self.assertEqual(response.status_code, 200, response.data)
        self.item = self.order.items.get()
        self.assertEqual(self.item.qty, 120)
