# -*- coding: utf-8 -*-
"""财务/库存核心不变量：资金流水守恒、整批原子性、出货关联不跨厂。"""
from decimal import Decimal
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer, Style
from apps.finance.models import Receivable, ReceiptLine
from apps.finance.services import (add_receipt, batch_receipt_by_customer,
                                   revoke_last_receipt, stock_valuation)
from apps.orders.models import Order, OrderItem
from apps.shipment.models import Shipment, ShipmentItem
from apps.production.models import Production
from apps.warehouse.models import (IssueItem, Lot, MaterialIssue,
                                   StockIn, StockInItem)


class FinancialInvariantTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username='fin-inv-owner', phone='13900007201', password='test-only')
        cls.company, _ = create_company_with_owner(cls.owner, '财务不变量隔离厂A')
        cls.owner_b = User.objects.create_user(
            username='fin-inv-owner-b', phone='13900007202', password='test-only')
        cls.company_b, _ = create_company_with_owner(cls.owner_b, '财务不变量隔离厂B')
        cls.customer = Customer.objects.create(
            company=cls.company, customer_no='FI-C1', name='财务客户A')
        cls.other_customer = Customer.objects.create(
            company=cls.company, customer_no='FI-C2', name='同厂另一客户')
        cls.style = Style.objects.create(
            company=cls.company, style_no='FI-S1', name='财务测试款')
        cls.order = Order.objects.create(
            company=cls.company, order_no='FI-ORDER', customer=cls.customer, style=cls.style)
        OrderItem.objects.create(order=cls.order, color='黑', size='M', qty=100, price='10')
        cls.shipment = Shipment.objects.create(
            company=cls.company, shipment_no='FI-SHIPMENT', order=cls.order,
            customer=cls.customer, total_qty=60, status=Shipment.Status.PARTIAL)
        ShipmentItem.objects.create(shipment=cls.shipment, color='黑', size='M', qty=60)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.client.credentials(HTTP_X_COMPANY_ID=str(self.company.pk))

    def _receivable(self, suffix, amount='100.00', customer=None):
        return Receivable.objects.create(
            company=self.company, receivable_no=f'FI-AR-{suffix}',
            customer=customer or self.customer, amount=amount)

    def test_receipts_and_reversals_conserve_received_amount(self):
        rec = self._receivable('FLOW')
        add_receipt(self.company, rec, amount='40.00', user=self.owner)
        add_receipt(self.company, rec, amount='60.00', user=self.owner)
        rec.refresh_from_db()
        self.assertEqual(rec.received_amount, Decimal('100.00'))
        self.assertEqual(rec.status, Receivable.Status.SETTLED)
        revoke_last_receipt(self.company, rec, user=self.owner)
        rec.refresh_from_db()
        live_total = sum(ReceiptLine.objects.filter(receivable=rec)
                         .values_list('amount', flat=True), Decimal('0'))
        self.assertEqual(live_total, Decimal('40.00'))
        self.assertEqual(rec.received_amount, live_total)
        self.assertEqual(rec.status, Receivable.Status.PARTIAL)
        self.assertEqual(ReceiptLine.all_objects.filter(receivable=rec).count(), 2)

    def test_batch_receipt_fifo_is_atomic_on_overpayment(self):
        first = self._receivable('FIFO-1', '60.00')
        second = self._receivable('FIFO-2', '40.00')
        result = batch_receipt_by_customer(
            self.company, self.customer.pk, total='100.00', user=self.owner)
        self.assertEqual([Decimal(x['amount']) for x in result['allocated']],
                         [Decimal('60.00'), Decimal('40.00')])
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, Receivable.Status.SETTLED)
        self.assertEqual(second.status, Receivable.Status.SETTLED)
        with self.assertRaises(Exception):
            batch_receipt_by_customer(
                self.company, self.customer.pk, total='0.01', user=self.owner)
        self.assertEqual(ReceiptLine.objects.filter(
            receivable__in=[first, second]).count(), 2)

    def test_voided_receivable_cannot_accept_payment(self):
        rec = self._receivable('VOID')
        rec.is_deleted = True
        rec.save(update_fields=['is_deleted', 'updated_at'])
        response = self.client.post(
            f'/api/finance/receivables/{rec.pk}/receipts', {'amount': '1.00'}, format='json')
        self.assertEqual(response.status_code, 404)
        self.assertFalse(ReceiptLine.objects.filter(receivable=rec).exists())

    def test_manual_receivable_cannot_link_foreign_shipment(self):
        order_b = Order.objects.create(company=self.company_b, order_no='FI-ORDER-B')
        shipment_b = Shipment.objects.create(
            company=self.company_b, shipment_no='FI-SHIPMENT-B', order=order_b, total_qty=1)
        response = self.client.post('/api/finance/receivables', {
            'customer': self.customer.pk, 'amount': '100.00',
            'shipment_id': shipment_b.pk,
        }, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('出货单', str(response.data))
        self.assertFalse(Receivable.objects.filter(shipment_id=shipment_b.pk).exists())

    def test_receivable_shipment_order_and_customer_must_match(self):
        other_order = Order.objects.create(
            company=self.company, order_no='FI-OTHER-ORDER',
            customer=self.other_customer, style=self.style)
        for payload in (
            {'customer': self.customer.pk, 'order': other_order.pk,
             'shipment_id': self.shipment.pk, 'amount': '10.00'},
            {'customer': self.other_customer.pk, 'order': self.order.pk,
             'shipment_id': self.shipment.pk, 'amount': '10.00'},
        ):
            response = self.client.post('/api/finance/receivables', payload, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Receivable.objects.filter(shipment=self.shipment).exists())

    def test_valid_manual_shipment_link_uses_company_owned_relationship(self):
        response = self.client.post('/api/finance/receivables', {
            'customer': self.customer.pk, 'order': self.order.pk,
            'shipment_id': self.shipment.pk, 'amount': '600.00',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        rec = Receivable.objects.get(shipment=self.shipment)
        self.assertEqual(rec.company, self.company)
        self.assertEqual(rec.order, self.order)
        self.assertEqual(rec.customer, self.customer)
        self.assertEqual(rec.amount, Decimal('600.00'))


class StockValuationInvariantTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user(
            username='stock-inv-owner', phone='13900007203', password='test-only')
        self.company, _ = create_company_with_owner(owner, '库存估值不变量隔离厂')
        self.style = Style.objects.create(
            company=self.company, style_no='FI-STOCK-S1', name='库存估值款')
        self.production = Production.objects.create(
            company=self.company, style=self.style, bed_no='FI-STOCK-BED',
            total_qty=100, plan_qty=100)
        self.lot = Lot.objects.create(
            company=self.company, lot_no='FI-LOT-1', fabric_name='估值面料', qty=0)
        self.yesterday = timezone.now() - timedelta(days=1)
        self._stock_in('FI-IN-1', self.yesterday, 10, '2.000')
        self._stock_in('FI-IN-2', timezone.now(), 10, '4.000')

    def _stock_in(self, number, at, qty, price, status=StockIn.Status.DONE):
        doc = StockIn.objects.create(
            company=self.company, in_no=number, in_at=at, status=status)
        StockInItem.objects.create(
            stock_in=doc, lot=self.lot, fabric_name='估值面料', lot_no=self.lot.lot_no,
            qty=qty, unit_price=price)
        return doc

    def _issue(self, number, kind, qty, status=MaterialIssue.Status.CONFIRMED, at=None):
        doc = MaterialIssue.objects.create(
            company=self.company, issue_no=number, type=kind, status=status,
            issue_at=at or timezone.now())
        if at is not None:
            # BaseModel.created_at 是建档时间，估值函数按它排序；显式构造历史流水。
            MaterialIssue.objects.filter(pk=doc.pk).update(created_at=at)
        IssueItem.objects.create(
            issue=doc, production=self.production, lot=self.lot, qty=qty)

    def test_weighted_average_issue_return_and_missing_price_conserve_value(self):
        StockIn.objects.filter(in_no='FI-IN-2').update(in_at=self.yesterday + timedelta(hours=1))
        self._issue('FI-ISSUE-1', MaterialIssue.Type.ISSUE, 5,
                    at=self.yesterday + timedelta(hours=2))
        self._issue('FI-RETURN-1', MaterialIssue.Type.RETURN, 2,
                    at=self.yesterday + timedelta(hours=3))
        self._stock_in('FI-IN-MISSING', timezone.now(), 100, None)
        # 草稿不能进入财务估值，即使数量/价格很大。
        self._stock_in('FI-IN-DRAFT', timezone.now(), 1000, '100.000',
                       status=StockIn.Status.DRAFT)
        self._issue('FI-ISSUE-DRAFT', MaterialIssue.Type.ISSUE, 1000,
                    status=MaterialIssue.Status.DRAFT)
        report = stock_valuation(self.company)
        self.assertEqual(len(report['rows']), 1)
        row = report['rows'][0]['lots'][0]
        self.assertEqual(row['in_qty'], '20.00')
        self.assertEqual(row['in_amt'], '60.00')
        self.assertEqual(row['issued_qty'], '3.00')
        self.assertEqual(row['balance_qty'], '17.00')
        self.assertEqual(row['balance_amt'], '51.00')
        self.assertEqual(row['wavg_price'], '3.000')
        self.assertEqual(row['missing_price_rows'], 1)

    def test_valuation_date_cutoff_excludes_later_flows(self):
        self._issue('FI-ISSUE-CUTOFF', MaterialIssue.Type.ISSUE, 5)
        report = stock_valuation(
            self.company, date_to=self.yesterday.date())
        row = report['rows'][0]['lots'][0]
        self.assertEqual(row['in_qty'], '10.00')
        self.assertEqual(row['in_amt'], '20.00')
        self.assertEqual(row['issued_qty'], '0.00')
        self.assertEqual(row['balance_qty'], '10.00')
        self.assertEqual(row['balance_amt'], '20.00')
        self.assertEqual(row['wavg_price'], '2.000')
