# -*- coding: utf-8 -*-
"""采购到应付、报工到实付工资的跨域端到端对拍。

业务动作均走 API；只使用 Django 隔离测试库。基础生产档案属于夹具，
不作为本套件宣称覆盖的端到端流程。
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Process, Style, Ticket
from apps.finance.models import CashEntry, Payable
from apps.production.models import Production, ProductionProcess
from apps.salary.models import SalaryPayment, Settlement
from apps.salary.payments import settlement_outstanding
from apps.warehouse.models import Lot, PurchaseItem, ReceiptItem, StockInItem


class CrossDomainFinanceE2ETests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username='finance-e2e-owner', phone='13900006101', password='test-only')
        cls.company, cls.admin_post = create_company_with_owner(
            cls.owner, '采购工资资金全链隔离厂')
        cls.worker = User.objects.create_user(
            username='finance-e2e-worker', phone='13900006102', password='test-only')
        UserCompany.objects.create(
            user=cls.worker, company=cls.company, post=cls.admin_post,
            status=UserCompany.Status.ACTIVE, hired_date=timezone.localdate())
        cls.style = Style.objects.create(
            company=cls.company, style_no='FIN-E2E-S1', name='资金全链测试款')
        cls.process = Process.objects.create(
            company=cls.company, name='资金全链缝制', default_price='2.500', is_fixed=True)
        cls.production = Production.objects.create(
            company=cls.company, style=cls.style, bed_no='FIN-E2E-B1', total_qty=10)
        ProductionProcess.objects.create(
            production=cls.production, process=cls.process, price='2.500')
        cls.ticket = Ticket.objects.create(
            company=cls.company, production=cls.production,
            bed_no=cls.production.bed_no, bundle_no='1', color='蓝', size='M',
            qty=10, qr_code='FIN-E2E-TICKET-1')

    def setUp(self):
        self.api = APIClient(raise_request_exception=False)
        self.api.force_authenticate(self.owner)
        self.api.credentials(HTTP_X_COMPANY_ID=str(self.company.id))
        self.today = timezone.localdate()

    def post(self, path, payload=None, expected=201):
        response = self.api.post(path, payload or {}, format='json')
        self.assertEqual(response.status_code, expected, response.content)
        return response.data

    def cash_flow(self):
        response = self.api.get(
            f'/api/finance/cash-flow?date_from={self.today.isoformat()}'
            f'&date_to={self.today.isoformat()}&group_by=day')
        self.assertEqual(response.status_code, 200, response.content)
        return response.data['totals']

    def test_purchase_payable_and_salary_payment_share_one_cash_flow(self):
        supplier = self.post('/api/suppliers', {
            'code': 'FIN-E2E-SUP', 'name': '资金全链面料商'})
        purchase = self.post('/api/purchase-orders', {
            'supplier': supplier['id'],
            'items': [{'fabric_name': '资金全链面料', 'qty': 10,
                       'unit': '米', 'price': '12.500'}],
        })
        purchase_item = purchase['items'][0]
        receipt = self.post('/api/receipts', {
            'supplier': supplier['id'], 'purchase': purchase['id'],
            'items': [{'purchase_item': purchase_item['id'],
                       'lot_no': 'FIN-E2E-LOT', 'arrived_qty': 10,
                       'qualified_qty': 8, 'rejected_qty': 2,
                       'remark': '两米拒收'}],
        })
        confirmed = self.post(f"/api/receipts/{receipt['id']}/confirm", expected=200)
        self.assertEqual(confirmed['status'], 'confirmed')

        stock_item = StockInItem.objects.get(
            stock_in__company=self.company, stock_in__purchase_id=purchase['id'])
        self.assertEqual(stock_item.qty, Decimal('8.00'))
        self.assertEqual(stock_item.unit_price, Decimal('12.500'))
        self.assertEqual(stock_item.qty * stock_item.unit_price, Decimal('100.000'))
        self.assertEqual(Lot.objects.get(
            company=self.company, lot_no='FIN-E2E-LOT').qty, Decimal('8.00'))
        purchase_item_obj = PurchaseItem.objects.get(pk=purchase_item['id'])
        self.assertEqual(purchase_item_obj.qualified_qty, Decimal('8.00'))
        self.assertEqual(ReceiptItem.objects.get(
            receipt_id=receipt['id']).rejected_qty, Decimal('2.00'))

        payable = self.post('/api/finance/payables', {
            'supplier': supplier['id'], 'purchase': purchase['id'],
            'amount': '100.00', 'payable_at': self.today.isoformat(),
            'remark': '按合格入库 8 米 × 12.5 元确认',
        })
        self.post('/api/finance/supplier-payments', {
            'payable': payable['id'], 'amount': '40.00', 'method': 'bank',
            'transaction_ref': 'FIN-E2E-AP-40',
            'paid_at': timezone.now().isoformat(),
        })
        payable_obj = Payable.objects.get(pk=payable['id'])
        self.assertEqual(payable_obj.purchase_id, purchase['id'])
        self.assertEqual(payable_obj.paid_amount, Decimal('40.00'))
        self.assertEqual(
            payable_obj.amount - payable_obj.paid_amount, Decimal('60.00'))

        self.post('/api/records', {
            'ticket_id': self.ticket.id, 'process_id': self.process.id,
            'user_id': self.worker.id, 'qty': 10,
        })
        settlement = self.post('/api/salary/settlements', {
            'period_start': self.today.isoformat(), 'period_end': self.today.isoformat(),
            'user_id': self.worker.id,
        })
        self.assertEqual(Decimal(settlement['amount']), Decimal('25.00'))
        salary_payment = self.post('/api/salary/payments', {
            'employee_id': self.worker.id, 'amount': '15.00', 'method': 'bank',
            'transaction_ref': 'FIN-E2E-SALARY-15',
            'allocations': [{'settlement_id': settlement['id'], 'amount': '15.00'}],
        })
        settlement_obj = salary_payment['allocations'][0]
        self.assertEqual(Decimal(settlement_obj['outstanding']), Decimal('10.00'))

        totals = self.cash_flow()
        self.assertEqual(totals['supplier_payments'], '-40.00')
        self.assertEqual(totals['salary_payments'], '-15.00')
        self.assertEqual(totals['outflow'], '55.00')
        self.assertEqual(totals['net'], '-55.00')
        # 工资和供应商付款直接来自各自事实表，不能再生成手工现金流水重复计数。
        self.assertFalse(CashEntry.objects.filter(company=self.company).exists())

        self.post(f"/api/salary/payments/{salary_payment['id']}/void", {
            'reason': '银行退汇，重新支付'}, expected=200)
        self.assertEqual(SalaryPayment.objects.get(
            pk=salary_payment['id']).status, SalaryPayment.Status.VOID)
        self.assertEqual(settlement_outstanding(
            Settlement.objects.get(pk=settlement['id']), self.worker.id), Decimal('25.00'))
        totals = self.cash_flow()
        self.assertEqual(totals['salary_payments'], '0.00')
        self.assertEqual(totals['outflow'], '40.00')

        self.post('/api/salary/payments', {
            'employee_id': self.worker.id, 'amount': '25.00', 'method': 'bank',
            'transaction_ref': 'FIN-E2E-SALARY-25',
            'allocations': [{'settlement_id': settlement['id'], 'amount': '25.00'}],
        })
        totals = self.cash_flow()
        self.assertEqual(totals['supplier_payments'], '-40.00')
        self.assertEqual(totals['salary_payments'], '-25.00')
        self.assertEqual(totals['outflow'], '65.00')
        self.assertEqual(totals['net'], '-65.00')
