from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Post, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.finance.models import CashEntry, Payable, Receivable, ReceiptLine, SupplierPayment
from apps.ops.models import AuditLog
from apps.salary.models import SalaryPayment
from apps.warehouse.models import PurchaseOrder, Supplier


class CashOperationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username='ap-owner', phone='13900009101',
                                             password='test-only')
        cls.company, _ = create_company_with_owner(cls.owner, '应付测试A')
        cls.viewer = User.objects.create_user(username='ap-viewer', phone='13900009102',
                                              password='test-only')
        cls.post = Post.objects.create(company=cls.company, name='财务查看',
                                       permissions=['finance.view'])
        UserCompany.objects.create(user=cls.viewer, company=cls.company, post=cls.post,
                                   status=UserCompany.Status.ACTIVE)
        cls.other = User.objects.create_user(username='ap-other', phone='13900009103',
                                             password='test-only')
        cls.other_company, _ = create_company_with_owner(cls.other, '应付测试B')
        cls.supplier = Supplier.objects.create(company=cls.company, code='S1', name='面料供应商')
        cls.other_supplier = Supplier.objects.create(company=cls.other_company,
                                                     code='S2', name='其他公司供应商')

    def api_client(self, user=None, company=None):
        client = APIClient()
        client.force_authenticate(user or self.owner)
        client.credentials(HTTP_X_COMPANY_ID=str((company or self.company).id))
        return client

    def create_payable(self, amount='1000.00'):
        response = self.api_client().post('/api/finance/payables', {
            'supplier': self.supplier.id, 'amount': amount,
            'payable_at': '2026-11-01', 'due_at': '2026-11-30',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        return response.data

    def pay(self, payable_id, amount='400.00', ref='BANK-1'):
        return self.api_client().post('/api/finance/supplier-payments', {
            'payable': payable_id, 'amount': amount, 'paid_at': '2026-11-02T10:00:00+08:00',
            'method': 'bank', 'transaction_ref': ref,
        }, format='json')

    def test_payable_payment_outstanding_and_audit(self):
        payable = self.create_payable()
        response = self.pay(payable['id'])
        self.assertEqual(response.status_code, 201, response.content)
        detail = self.api_client().get(f"/api/finance/payables/{payable['id']}")
        self.assertEqual(detail.data['paid_amount'], '400.00')
        self.assertEqual(detail.data['outstanding'], '600.00')
        self.assertEqual(detail.data['status'], 'partial')
        self.assertTrue(AuditLog.objects.filter(message__contains='供应商付款').exists())
        self.assertEqual(self.pay(payable['id'], '600.01', 'BANK-2').status_code, 400)
        self.assertEqual(self.pay(payable['id'], '600.00', 'BANK-2').status_code, 201)
        self.assertEqual(Payable.objects.get(id=payable['id']).status, 'settled')

    def test_void_restores_outstanding_without_deleting_fact(self):
        payable = self.create_payable()
        payment = self.pay(payable['id']).data
        response = self.api_client().post(
            f"/api/finance/supplier-payments/{payment['id']}/void",
            {'reason': '银行退单'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        payment_obj = SupplierPayment.objects.get(id=payment['id'])
        self.assertEqual(payment_obj.status, 'voided')
        self.assertEqual(payment_obj.void_reason, '银行退单')
        self.assertEqual(Payable.objects.get(id=payable['id']).paid_amount, Decimal('0'))
        self.assertEqual(self.api_client().post(
            f"/api/finance/supplier-payments/{payment['id']}/void",
            {'reason': '再次作废'}, format='json').status_code, 400)

    def test_duplicate_reference_and_voided_reference(self):
        payable = self.create_payable()
        self.pay(payable['id'], ref='UNIQUE-BANK')
        self.assertEqual(self.pay(payable['id'], ref='UNIQUE-BANK').status_code, 400)
        self.assertEqual(SupplierPayment.objects.filter(company=self.company).count(), 1)

    def test_company_supplier_purchase_and_permission_checks(self):
        response = self.api_client().post('/api/finance/payables', {
            'supplier': self.other_supplier.id, 'amount': '10.00',
            'payable_at': '2026-11-01'}, format='json')
        self.assertEqual(response.status_code, 400)
        payable = self.create_payable()
        other_client = self.api_client(self.other, self.other_company)
        self.assertEqual(other_client.get(f"/api/finance/payables/{payable['id']}").status_code, 404)
        self.assertEqual(self.api_client(self.viewer).get('/api/finance/payables').status_code, 200)
        self.assertEqual(self.api_client(self.viewer).post('/api/finance/payables', {
            'supplier': self.supplier.id, 'amount': '10', 'payable_at': '2026-11-01'
        }, format='json').status_code, 403)
        self.assertEqual(APIClient().get('/api/finance/payables').status_code, 401)

    def test_payable_requires_payment_void_before_void(self):
        payable = self.create_payable()
        payment = self.pay(payable['id']).data
        self.assertEqual(self.api_client().post(
            f"/api/finance/payables/{payable['id']}/void", {}, format='json').status_code, 400)
        self.api_client().post(f"/api/finance/supplier-payments/{payment['id']}/void",
                           {'reason': '冲销'}, format='json')
        self.assertEqual(self.api_client().post(
            f"/api/finance/payables/{payable['id']}/void", {}, format='json').status_code, 200)
        self.assertTrue(Payable.all_objects.get(id=payable['id']).is_deleted)

    def test_cash_expense_income_filters_duplicate_and_void(self):
        expense = self.api_client().post('/api/finance/cash-entries', {
            'direction': 'expense', 'category': '房租', 'amount': '5000.00',
            'occurred_at': '2026-11-01T09:00:00+08:00', 'reference_no': 'INV-1',
        }, format='json')
        self.assertEqual(expense.status_code, 201, expense.content)
        income = self.api_client().post('/api/finance/cash-entries', {
            'direction': 'income', 'category': '废料收入', 'amount': '80.00',
        }, format='json')
        self.assertEqual(income.status_code, 201, income.content)
        duplicate = self.api_client().post('/api/finance/cash-entries', {
            'direction': 'expense', 'category': '房租', 'amount': '5000.00',
            'reference_no': 'INV-1'}, format='json')
        self.assertEqual(duplicate.status_code, 400)
        listed = self.api_client().get('/api/finance/cash-entries?direction=expense')
        self.assertEqual(listed.data['count'], 1)
        voided = self.api_client().post(
            f"/api/finance/cash-entries/{expense.data['id']}/void",
            {'reason': '重复凭证'}, format='json')
        self.assertEqual(voided.status_code, 200)
        self.assertEqual(CashEntry.objects.get(id=expense.data['id']).status, 'voided')

    def test_cash_flow_merges_real_sources_and_requires_salary_permission(self):
        occurred = datetime(2026, 11, 5, 9, tzinfo=dt_timezone.utc)
        receivable = Receivable.objects.create(
            company=self.company, receivable_no='AR-CF', amount='200.00')
        ReceiptLine.objects.create(company=self.company, receivable=receivable,
                                   amount='200.00', received_at=occurred)
        CashEntry.objects.create(company=self.company, entry_no='CF-IN', direction='income',
                                 category='其他', amount='50.00', occurred_at=occurred)
        CashEntry.objects.create(company=self.company, entry_no='CF-OUT', direction='expense',
                                 category='房租', amount='30.00', occurred_at=occurred)
        CashEntry.objects.create(company=self.company, entry_no='CF-VOID', direction='expense',
                                 category='房租', amount='999.00', occurred_at=occurred,
                                 status='voided')
        SupplierPayment.objects.create(company=self.company, supplier_name='供应商',
                                       amount='60.00', paid_at=occurred)
        SalaryPayment.objects.create(company=self.company, employee=self.owner,
                                     amount='80.00', paid_at=occurred)
        url = '/api/finance/cash-flow?date_from=2026-11-01&date_to=2026-11-30&group_by=month'
        response = self.api_client().get(url)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['totals']['income'], '250.00')
        self.assertEqual(response.data['totals']['outflow'], '170.00')
        self.assertEqual(response.data['totals']['net'], '80.00')
        self.assertEqual(response.data['totals']['customer_receipts'], '200.00')
        self.assertEqual(len(response.data['rows']), 1)
        self.assertEqual(self.api_client(self.viewer).get(url).status_code, 403)
        self.post.permissions = ['finance.manage']
        self.post.save(update_fields=['permissions'])
        self.assertEqual(self.api_client(self.viewer).get(url).status_code, 403)
        other = self.api_client(self.other, self.other_company).get(url)
        self.assertEqual(other.status_code, 200)
        self.assertEqual(other.data['totals']['net'], '0.00')
        self.assertEqual(self.api_client().get('/api/finance/cash-flow').status_code, 400)

    def test_unallocated_payment_and_invalid_amount(self):
        response = self.api_client().post('/api/finance/supplier-payments', {
            'supplier_name': '临时承运商', 'amount': '100.00',
            'method': 'cash'}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIsNone(response.data['payable'])
        self.assertEqual(self.api_client().post('/api/finance/cash-entries', {
            'direction': 'expense', 'category': '电费', 'amount': '0'
        }, format='json').status_code, 400)
