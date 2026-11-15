# -*- coding: utf-8 -*-
"""PostgreSQL 真并发门禁：不能用 SQLite 的单写者行为替代。

每个用例通过两个独立线程、独立 APIClient/数据库连接同时提交，并直接核对
业务不变量。线程入口加同步屏障；关键场景在提交前增加短暂停顿，稳定制造
“两个请求都已通过前置读取”的交错，而不是依赖机器调度碰运气。

只运行于 run_postgres_regression.py 的独立测试库；SQLite 全套件中显式跳过。
"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from itertools import count
import threading
import time
from unittest.mock import patch

from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer, Process, Style, Ticket
from apps.finance.models import ReceiptLine, Receivable
from apps.orders.models import Order, OrderItem
from apps.production.models import Production, ProductionProcess
from apps.records.models import Record
from apps.salary.models import Settlement
from apps.shipment.models import Shipment, ShipmentItem
from apps.warehouse.models import (
    IssueItem, Lot, MaterialIssue, ProductionLotLink, PurchaseItem,
    PurchaseOrder, Receipt, ReceiptItem, StockIn, StockInItem,
)


class PostgresConcurrencyTests(TransactionTestCase):
    """TransactionTestCase 不包裹外层事务，线程才能看到彼此已提交的数据。"""

    reset_sequences = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != 'postgresql':
            raise cls.skipTestClass('PostgreSQL 行锁与并发语义专项，SQLite 不执行')

    def setUp(self):
        self.owner = User.objects.create_user(
            username='pg-race-owner', phone='13900008001', password='test-only')
        self.company, self.admin_post = create_company_with_owner(
            self.owner, 'PostgreSQL并发测试厂')
        self.worker = User.objects.create_user(
            username='pg-race-worker', phone='13900008002', password='test-only')
        self.member = UserCompany.objects.create(
            user=self.worker, company=self.company, post=self.admin_post,
            status=UserCompany.Status.ACTIVE, hired_date=timezone.localdate())
        self.style = Style.objects.create(
            company=self.company, style_no='PG-RACE-S1', name='并发测试款')
        self.process = Process.objects.create(
            company=self.company, name='并发缝制', default_price='2.500', sort=20)
        self.production = Production.objects.create(
            company=self.company, style=self.style, bed_no='PG-RACE-B1',
            total_qty=100, plan_qty=100)
        ProductionProcess.objects.create(
            production=self.production, process=self.process, price='2.500')
        self.ticket = Ticket.objects.create(
            company=self.company, production=self.production,
            bed_no=self.production.bed_no, bundle_no='1', color='红', size='M',
            qty=100, qr_code=f'{self.company.company_no}|PG-RACE-B1|PG-RACE-S1|1|红|M|100')

    def _post(self, path, payload=None, user_id=None):
        """必须在新线程内新建 client，不能跨线程共享 APIClient。"""
        close_old_connections()
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(user=User.objects.get(pk=user_id or self.owner.pk))
        client.credentials(HTTP_X_COMPANY_ID=str(self.company.pk))
        try:
            response = client.post(path, payload or {}, format='json')
            return response.status_code, response.data
        finally:
            # 立即关闭本线程连接，不能等 CONN_MAX_AGE 到期，也不能因异常泄漏。
            connections.close_all()

    def _put(self, path, payload=None, user_id=None):
        close_old_connections()
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(user=User.objects.get(pk=user_id or self.owner.pk))
        client.credentials(HTTP_X_COMPANY_ID=str(self.company.pk))
        try:
            response = client.put(path, payload or {}, format='json')
            return response.status_code, response.data
        finally:
            connections.close_all()

    def _race(self, calls):
        barrier = threading.Barrier(len(calls))

        def call(fn):
            barrier.wait(timeout=10)
            return fn()

        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            futures = [pool.submit(call, fn) for fn in calls]
            results = [f.result(timeout=30) for f in futures]
        return results

    def _assert_statuses(self, results, expected):
        statuses = sorted(result[0] for result in results)
        self.assertEqual(statuses, sorted(expected),
                         msg=f'并发响应不符合预期: {results}')

    def test_scan_same_ticket_cannot_exceed_bundle_qty(self):
        calls = [lambda: self._post('/api/records', {
            'ticket_id': self.ticket.pk, 'process_id': self.process.pk,
            'user_id': self.worker.pk, 'qty': 60,
        }, user_id=self.worker.pk) for _ in range(2)]
        results = self._race(calls)
        self._assert_statuses(results, [201, 400])
        record = Record.objects.get(company=self.company, ticket=self.ticket,
                                    process=self.process, user=self.worker)
        self.assertEqual(record.qty, 60)
        self.assertEqual(Record.objects.filter(ticket=self.ticket).count(), 1)

    def test_material_issue_double_confirm_updates_usage_once(self):
        lot = Lot.objects.create(company=self.company, lot_no='PG-RACE-LOT', qty=100)
        issue = MaterialIssue.objects.create(
            company=self.company, issue_no='PG-RACE-ISSUE', issue_at=timezone.now())
        IssueItem.objects.create(issue=issue, production=self.production, lot=lot, qty=5)
        # 旧实现会在此处让两个已完成用量累加的线程同时停留，稳定暴露重复确认；
        # 修复后第二线程被单据行锁挡住，只有第一线程进入此钩子。
        with patch('apps.cost.services.mark_issue_confirmed_stale',
                   side_effect=lambda *args, **kwargs: time.sleep(0.15)):
            results = self._race([
                lambda: self._post(f'/api/material-issues/{issue.pk}/confirm')
                for _ in range(2)
            ])
        self._assert_statuses(results, [200, 400])
        issue.refresh_from_db()
        link = ProductionLotLink.objects.get(production=self.production, lot=lot)
        self.assertEqual(issue.status, MaterialIssue.Status.CONFIRMED)
        self.assertEqual(link.qty, Decimal('5.00'))
        self.assertEqual(ProductionLotLink.objects.filter(
            production=self.production, lot=lot).count(), 1)

    def test_receipt_double_confirm_creates_one_stock_in(self):
        po = PurchaseOrder.objects.create(
            company=self.company, order_no='PG-RACE-PO', ordered_at=timezone.localdate())
        item = PurchaseItem.objects.create(
            order=po, fabric_name='测试面料', qty=20, price='3.000')
        receipt = Receipt.objects.create(company=self.company, receipt_no='PG-RACE-RECEIPT')
        ReceiptItem.objects.create(
            receipt=receipt, purchase_item=item, fabric_name='测试面料',
            lot_no='PG-RACE-IQC-LOT', arrived_qty=10, qualified_qty=8, rejected_qty=2)
        numbers = count(1)

        def slow_unique_number(*args, **kwargs):
            time.sleep(0.15)
            return f'PG-RACE-RK-{next(numbers)}'

        # 隔离单号生成冲突，专测重复确认；两个旧事务都能拿到不同单号并提交。
        with patch('apps.warehouse.views._next_doc_no', side_effect=slow_unique_number):
            results = self._race([
                lambda: self._post(f'/api/receipts/{receipt.pk}/confirm')
                for _ in range(2)
            ])
        self._assert_statuses(results, [200, 400])
        receipt.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(receipt.status, Receipt.Status.CONFIRMED)
        self.assertEqual(StockIn.objects.filter(
            company=self.company, remark__contains=receipt.receipt_no).count(), 1)
        self.assertEqual(StockInItem.objects.filter(
            stock_in__company=self.company, lot_no='PG-RACE-IQC-LOT').count(), 1)
        self.assertEqual(item.arrived_qty, Decimal('10.00'))
        self.assertEqual(item.qualified_qty, Decimal('8.00'))

    def _shipment_fixture(self):
        customer = Customer.objects.create(
            company=self.company, customer_no='PG-RACE-C1', name='并发客户')
        order = Order.objects.create(
            company=self.company, order_no='PG-RACE-ORDER', customer=customer,
            style=self.style)
        OrderItem.objects.create(order=order, color='红', size='M', qty=100, price='10')
        self.production.order = order
        self.production.save(update_fields=['order'])
        Record.objects.create(
            company=self.company, ticket=self.ticket, process=self.process,
            user=self.worker, qty=100, price='2.500')
        shipments = []
        for index in range(2):
            shipment = Shipment.objects.create(
                company=self.company, shipment_no=f'PG-RACE-SH-{index}',
                order=order, customer=customer, total_qty=60)
            ShipmentItem.objects.create(shipment=shipment, color='红', size='M', qty=60)
            shipments.append(shipment)
        return customer, order, shipments

    def test_two_shipments_cannot_overship_same_order(self):
        _customer, order, shipments = self._shipment_fixture()
        from apps.finance.services import create_receivable

        def slow_receivable(*args, **kwargs):
            # 两线程均已完成未交量读取后才建应收：旧实现会双双审核 60+60。
            time.sleep(0.15)
            return create_receivable(*args, **kwargs)

        with patch('apps.finance.services.create_receivable', side_effect=slow_receivable):
            results = self._race([
                lambda shipment=shipment: self._post(f'/api/shipments/{shipment.pk}/audit')
                for shipment in shipments
            ])
        self._assert_statuses(results, [200, 400])
        self.assertEqual(ShipmentItem.objects.filter(
            shipment__order=order, shipment__is_deleted=False,
        ).exclude(shipment__status=Shipment.Status.DRAFT).count(), 1)
        self.assertEqual(sum(Shipment.objects.filter(order=order).exclude(
            status=Shipment.Status.DRAFT).values_list('total_qty', flat=True)), 60)
        self.assertEqual(Receivable.objects.filter(order=order).count(), 1)

    def test_order_reduction_and_shipment_audit_are_serialized(self):
        """改单先校验并持锁时，随后出货必须按新订单量重新校验并拒绝。"""
        customer = Customer.objects.create(
            company=self.company, customer_no='PG-RACE-EDIT-C1', name='改单并发客户')
        order = Order.objects.create(
            company=self.company, order_no='PG-RACE-EDIT-ORDER',
            customer=customer, style=self.style)
        OrderItem.objects.create(order=order, color='红', size='M', qty=100, price='10')
        shipment = Shipment.objects.create(
            company=self.company, shipment_no='PG-RACE-EDIT-SH',
            order=order, customer=customer, total_qty=60)
        ShipmentItem.objects.create(shipment=shipment, color='红', size='M', qty=60)
        validated = threading.Event()
        from apps.orders.views import _validate_downstream_quantities

        def slow_validation(*args, **kwargs):
            result = _validate_downstream_quantities(*args, **kwargs)
            validated.set()
            time.sleep(0.15)
            return result

        def audit_after_order_validation():
            if not validated.wait(timeout=10):
                raise RuntimeError('改单校验未进入，无法构造确定交错')
            return self._post(f'/api/shipments/{shipment.pk}/audit')

        payload = {'style': self.style.pk,
                   'items': [{'color': '红', 'size': 'M', 'qty': 50, 'price': '10'}]}
        with patch('apps.orders.views._validate_downstream_quantities',
                   side_effect=slow_validation), patch(
                       'apps.shipment.services.produced_qty_map',
                       return_value={('红', 'M'): 100}):
            results = self._race([
                lambda: self._put(f'/api/orders/{order.pk}', payload),
                audit_after_order_validation,
            ])
        self._assert_statuses(results, [200, 400])
        order.refresh_from_db()
        shipment.refresh_from_db()
        self.assertEqual(order.items.get().qty, 50)
        self.assertEqual(shipment.status, Shipment.Status.DRAFT)
        self.assertEqual(Receivable.objects.filter(order=order).count(), 0)

    def test_concurrent_receipts_cannot_overpay_receivable(self):
        customer = Customer.objects.create(
            company=self.company, customer_no='PG-RACE-AR-C1', name='收款客户')
        receivable = Receivable.objects.create(
            company=self.company, receivable_no='PG-RACE-AR', customer=customer,
            amount='100.00')
        results = self._race([
            lambda: self._post(f'/api/finance/receivables/{receivable.pk}/receipts',
                               {'amount': '60.00', 'method': 'cash'})
            for _ in range(2)
        ])
        self._assert_statuses(results, [201, 400])
        receivable.refresh_from_db()
        self.assertEqual(receivable.received_amount, Decimal('60.00'))
        self.assertEqual(receivable.status, Receivable.Status.PARTIAL)
        self.assertEqual(ReceiptLine.objects.filter(receivable=receivable).count(), 1)

    def test_process_wage_cannot_be_settled_twice(self):
        record = Record.objects.create(
            company=self.company, ticket=self.ticket, process=self.process,
            user=self.worker, qty=40, price='2.500')
        from apps.salary.views import compute_wage

        def slow_wage(*args, **kwargs):
            result = compute_wage(*args, **kwargs)
            time.sleep(0.15)
            return result

        payload = {'period_start': str(timezone.localdate()),
                   'period_end': str(timezone.localdate()), 'user_id': self.worker.pk}
        with patch('apps.salary.views.compute_wage', side_effect=slow_wage):
            results = self._race([
                lambda: self._post('/api/salary/settlements', payload) for _ in range(2)
            ])
        self._assert_statuses(results, [201, 400])
        record.refresh_from_db()
        self.assertIsNotNone(record.settlement_id)
        self.assertEqual(Settlement.objects.filter(
            company=self.company, user=self.worker,
            status=Settlement.Status.SETTLED).count(), 1)
        self.assertEqual(Settlement.objects.get(pk=record.settlement_id).amount,
                         Decimal('100.00'))

    def test_fixed_wage_cannot_be_settled_twice(self):
        self.member.salary_type = UserCompany.SalaryType.FIXED
        self.member.fixed_salary = Decimal('3000.00')
        self.member.fixed_type = UserCompany.FixedType.MONTH
        self.member.save(update_fields=['salary_type', 'fixed_salary', 'fixed_type'])
        from apps.salary.views import _outstanding_wage

        def slow_outstanding(*args, **kwargs):
            result = _outstanding_wage(*args, **kwargs)
            time.sleep(0.15)
            return result

        payload = {'period_start': str(timezone.localdate().replace(day=1)),
                   'period_end': str(timezone.localdate()), 'user_id': self.worker.pk}
        with patch('apps.salary.views._outstanding_wage', side_effect=slow_outstanding):
            results = self._race([
                lambda: self._post('/api/salary/settlements', payload) for _ in range(2)
            ])
        self._assert_statuses(results, [201, 400])
        settlements = Settlement.objects.filter(
            company=self.company, user=self.worker,
            status=Settlement.Status.SETTLED)
        self.assertEqual(settlements.count(), 1)
        self.assertEqual(settlements.get().amount, Decimal('3000.00'))
