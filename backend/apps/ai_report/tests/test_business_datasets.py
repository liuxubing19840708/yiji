# -*- coding: utf-8 -*-
"""P0 采购、薪酬与应收回款：认证口径、权限、单位及租户隔离。"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Post, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer
from apps.finance.models import Receivable, ReceiptLine
from apps.salary.models import SettleApply, Settlement
from apps.tech.models import Material
from apps.warehouse.models import (
    PurchaseItem, PurchaseOrder, Receipt, ReceiptItem, Supplier,
)


class BusinessDatasetApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.owner = User.objects.create_user(
            username='p0-report-owner', phone='13900008501', password='test-only')
        cls.company, _ = create_company_with_owner(cls.owner, 'P0报表A厂')
        post = Post.objects.create(
            company=cls.company, name='P0分析岗',
            permissions=['warehouse.manage', 'salary.view', 'finance.view', 'ai.export'])
        plain_post = Post.objects.create(company=cls.company, name='P0普工', permissions=[])
        cls.viewer = User.objects.create_user(
            username='p0-report-viewer', phone='13900008502', password='test-only',
            real_name='报表员工')
        cls.worker = User.objects.create_user(
            username='p0-report-worker', phone='13900008503', password='test-only')
        UserCompany.objects.create(
            user=cls.viewer, company=cls.company, post=post,
            status=UserCompany.Status.ACTIVE)
        UserCompany.objects.create(
            user=cls.worker, company=cls.company, post=plain_post,
            status=UserCompany.Status.ACTIVE)

        supplier = Supplier.objects.create(
            company=cls.company, code='P0-S1', name='P0供应商')
        material = Material.objects.create(
            company=cls.company, code='P0-M1', name='针织布', unit='公斤',
            unit_alt='米', weight_gsm='200', width_cm='160')
        po1 = PurchaseOrder.objects.create(
            company=cls.company, order_no='P0-PO1', supplier=supplier,
            ordered_at=cls.today, status=PurchaseOrder.Status.DONE)
        po2 = PurchaseOrder.objects.create(
            company=cls.company, order_no='P0-PO2', supplier=supplier,
            ordered_at=cls.today - timedelta(days=1))
        cls.item_kg = PurchaseItem.objects.create(
            order=po1, fabric_name='针织布', qty='100', unit='公斤', price='10',
            material=material)
        cls.item_m = PurchaseItem.objects.create(
            order=po1, fabric_name='针织布', qty='50', unit='米', price='10',
            material=material)
        cls.item_over = PurchaseItem.objects.create(
            order=po2, fabric_name='针织布', qty='20', unit='kg', price='0',
            material=material)
        cls.item_pending = PurchaseItem.objects.create(
            order=po2, fabric_name='未知布', qty='7', unit='码', price='99')
        PurchaseItem.objects.create(
            order=po2, fabric_name='删除行', qty='500', unit='公斤', is_deleted=True)

        confirmed = Receipt.objects.create(
            company=cls.company, receipt_no='P0-R1', purchase=po1,
            status=Receipt.Status.CONFIRMED, confirmed_at=timezone.now())
        draft = Receipt.objects.create(
            company=cls.company, receipt_no='P0-R2', purchase=po1)
        deleted = Receipt.objects.create(
            company=cls.company, receipt_no='P0-R3', purchase=po1,
            status=Receipt.Status.CONFIRMED, is_deleted=True)
        ReceiptItem.objects.bulk_create([
            ReceiptItem(receipt=confirmed, purchase_item=cls.item_kg, material=material,
                        lot_no='P0-L1', unit='公斤', arrived_qty='30',
                        qualified_qty='28', rejected_qty='2'),
            ReceiptItem(receipt=confirmed, purchase_item=cls.item_m, material=material,
                        lot_no='P0-L2', unit='米', arrived_qty='10',
                        qualified_qty='9', rejected_qty='1'),
            ReceiptItem(receipt=confirmed, purchase_item=cls.item_over, material=material,
                        lot_no='P0-L3', unit='kg', arrived_qty='25',
                        qualified_qty='25', rejected_qty='0'),
            ReceiptItem(receipt=draft, purchase_item=cls.item_kg, material=material,
                        lot_no='P0-L4', unit='公斤', arrived_qty='500'),
            ReceiptItem(receipt=deleted, purchase_item=cls.item_kg, material=material,
                        lot_no='P0-L5', unit='公斤', arrived_qty='500'),
        ])

        Settlement.objects.create(
            company=cls.company, period_start=cls.today, period_end=cls.today,
            amount='1000', employee_count=2, wage_detail={'1': '600', '2': '400'})
        Settlement.objects.create(
            company=cls.company, user=cls.viewer, period_start=cls.today,
            period_end=cls.today, amount='300', employee_count=1)
        Settlement.objects.create(
            company=cls.company, user=cls.viewer, period_start=cls.today,
            period_end=cls.today, amount='900', employee_count=1,
            status=Settlement.Status.REVOKED)
        for status in SettleApply.Status:
            SettleApply.objects.create(
                company=cls.company, applicant=cls.viewer, user=cls.viewer,
                period_start=cls.today, period_end=cls.today, amount='123', status=status)

        customer = Customer.objects.create(
            company=cls.company, customer_no='P0-C1', name='P0客户')
        ar1 = Receivable.objects.create(
            company=cls.company, receivable_no='P0-AR1', customer=customer,
            amount='1000', received_amount='250', status=Receivable.Status.PARTIAL)
        ar2 = Receivable.objects.create(
            company=cls.company, receivable_no='P0-AR2', customer=customer,
            amount='500', received_amount='500', status=Receivable.Status.SETTLED)
        ar3 = Receivable.objects.create(
            company=cls.company, receivable_no='P0-AR3', customer=customer,
            amount='200', received_amount='0')
        deleted_ar = Receivable.objects.create(
            company=cls.company, receivable_no='P0-AR4', customer=customer,
            amount='999', received_amount='888', is_deleted=True)
        ReceiptLine.objects.bulk_create([
            ReceiptLine(company=cls.company, receivable=ar1, amount='100',
                        received_at=timezone.now(), method=ReceiptLine.Method.CASH),
            ReceiptLine(company=cls.company, receivable=ar1, amount='150',
                        received_at=timezone.now() - timedelta(days=1),
                        method=ReceiptLine.Method.MONTHLY),
            ReceiptLine(company=cls.company, receivable=ar2, amount='500',
                        received_at=timezone.now(), method=ReceiptLine.Method.ACCEPTANCE),
            ReceiptLine(company=cls.company, receivable=ar1, amount='900',
                        received_at=timezone.now(), is_deleted=True),
            ReceiptLine(company=cls.company, receivable=deleted_ar, amount='888',
                        received_at=timezone.now()),
        ])

        other_owner = User.objects.create_user(
            username='p0-report-other', phone='13900008504', password='test-only')
        other_company, _ = create_company_with_owner(other_owner, 'P0报表B厂')
        other_po = PurchaseOrder.objects.create(
            company=other_company, order_no='P0-OTHER', ordered_at=cls.today)
        PurchaseItem.objects.create(
            order=other_po, fabric_name='他厂面料', qty='999', unit='公斤')
        Receivable.objects.create(
            company=other_company, receivable_no='P0-OTHER-AR', amount='999')

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        client.credentials(HTTP_X_COMPANY_ID=str(self.company.id))
        return client

    @staticmethod
    def plan(dataset, metrics, dimensions=(), **patch):
        value = {
            'dataset': dataset,
            'metrics': [
                {'key': key, 'aggregation': aggregation}
                for key, aggregation in metrics
            ],
            'dimensions': list(dimensions), 'filters': [], 'order_by': [],
            'limit': 100, 'include_deleted': False, 'presentation': 'table',
        }
        value.update(patch)
        return value

    def execute(self, dataset, metrics, dimensions=(), user=None, expected=200, **patch):
        response = self.client_for(user or self.viewer).post(
            '/api/ai/report/execute',
            {'plan': self.plan(dataset, metrics, dimensions, **patch)}, format='json')
        self.assertEqual(response.status_code, expected, response.content)
        return response

    @staticmethod
    def rows(response):
        keys = [column['key'] for column in response.data['report']['columns']]
        return [dict(zip(keys, row)) for row in response.data['report']['rows']]

    def test_purchase_flows_amount_and_pending_rows(self):
        response = self.execute('purchase_items', [
            ('purchase_order_count', 'count_distinct'),
            ('purchase_qty', 'sum'),
            ('confirmed_arrived_qty', 'sum'),
            ('confirmed_qualified_qty', 'sum'),
            ('confirmed_rejected_qty', 'sum'),
            ('pending_receipt_qty', 'sum'),
        ], ['unit'])
        rows = {row['unit']: row for row in self.rows(response)}
        self.assertEqual(Decimal(rows['公斤']['purchase_qty']), Decimal('100'))
        self.assertEqual(Decimal(rows['公斤']['confirmed_arrived_qty']), Decimal('30'))
        self.assertEqual(Decimal(rows['公斤']['pending_receipt_qty']), Decimal('70'))
        self.assertEqual(Decimal(rows['米']['confirmed_arrived_qty']), Decimal('10'))
        self.assertEqual(Decimal(rows['kg']['pending_receipt_qty']), Decimal('0'))
        self.assertEqual(Decimal(rows['码']['purchase_qty']), Decimal('7'))

        amount = self.rows(self.execute('purchase_items', [
            ('convertible_purchase_amount', 'sum'),
            ('amount_pending_line_count', 'count'),
        ]))[0]
        self.assertEqual(Decimal(amount['convertible_purchase_amount']), Decimal('1160'))
        self.assertEqual(amount['amount_pending_line_count'], 1)
        self.execute('purchase_items', [('purchase_qty', 'sum')], expected=400)

    def test_salary_snapshots_and_application_states(self):
        row = self.rows(self.execute('salary_settlements', [
            ('settlement_count', 'count'), ('settled_count', 'count'),
            ('settled_amount', 'sum'), ('revoked_amount', 'sum'),
            ('settled_employee_count', 'sum'),
        ]))[0]
        self.assertEqual(row['settlement_count'], 3)
        self.assertEqual(row['settled_count'], 2)
        self.assertEqual(Decimal(row['settled_amount']), Decimal('1300'))
        self.assertEqual(Decimal(row['revoked_amount']), Decimal('900'))
        self.assertEqual(row['settled_employee_count'], 3)

        row = self.rows(self.execute('salary_applications', [
            ('application_count', 'count'), ('pending_count', 'count'),
            ('approved_count', 'count'), ('paid_count', 'count'),
            ('rejected_count', 'count'), ('voided_count', 'count'),
        ]))[0]
        self.assertEqual(row, {
            'application_count': 5, 'pending_count': 1, 'approved_count': 1,
            'paid_count': 1, 'rejected_count': 1, 'voided_count': 1,
        })

    def test_receivables_and_period_receipts(self):
        row = self.rows(self.execute('finance_receivables', [
            ('receivable_count', 'count'), ('receivable_amount', 'sum'),
            ('received_amount', 'sum'), ('outstanding_amount', 'sum'),
        ]))[0]
        self.assertEqual(row['receivable_count'], 3)
        self.assertEqual(Decimal(row['receivable_amount']), Decimal('1700'))
        self.assertEqual(Decimal(row['received_amount']), Decimal('750'))
        self.assertEqual(Decimal(row['outstanding_amount']), Decimal('950'))

        rows = self.rows(self.execute('finance_receipts', [
            ('receipt_count', 'count'), ('received_amount', 'sum'),
        ], ['method'], order_by=[{'field': 'method', 'direction': 'asc'}]))
        self.assertEqual([(row['method'], Decimal(row['received_amount'])) for row in rows], [
            ('acceptance', Decimal('500')), ('cash', Decimal('100')),
            ('monthly', Decimal('150')),
        ])
        today_rows = self.rows(self.execute(
            'finance_receipts', [('received_amount', 'sum')], filters=[{
                'field': 'received_at', 'operator': 'between',
                'value': [self.today.isoformat(), self.today.isoformat()],
            }]))
        self.assertEqual(Decimal(today_rows[0]['received_amount']), Decimal('600'))

    def test_natural_language_uses_certified_business_metrics(self):
        cases = {
            '各供应商采购数量': ('purchase_items', ['supplier_name', 'unit']),
            '各员工有效结算金额': ('salary_settlements', ['employee_name']),
            '各状态结算申请笔数': ('salary_applications', ['status']),
            '各客户应收余额': ('finance_receivables', ['customer_name']),
            '各收款方式回款金额': ('finance_receipts', ['method']),
        }
        for question, (dataset, dimensions) in cases.items():
            with self.subTest(question=question):
                response = self.client_for(self.viewer).post('/api/ai/report/query', {
                    'question': question, 'history': [], 'mode': 'query_plan',
                }, format='json')
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.data['planner']['source'], 'local')
                self.assertEqual(response.data['plan']['dataset'], dataset)
                self.assertEqual(response.data['plan']['dimensions'], dimensions)

    def test_permissions_soft_delete_and_company_scope(self):
        owner_keys = {item['key'] for item in
                      self.client_for(self.owner).get('/api/ai/report/catalog').data['datasets']}
        worker_keys = {item['key'] for item in
                       self.client_for(self.worker).get('/api/ai/report/catalog').data['datasets']}
        expected = {
            'purchase_items', 'salary_settlements', 'salary_applications',
            'finance_receivables', 'finance_receipts',
        }
        self.assertTrue(expected <= owner_keys)
        self.assertTrue(expected.isdisjoint(worker_keys))
        for dataset, metric in (
            ('purchase_items', 'purchase_line_count'),
            ('salary_settlements', 'settlement_count'),
            ('salary_applications', 'application_count'),
            ('finance_receivables', 'receivable_count'),
            ('finance_receipts', 'receipt_count'),
        ):
            with self.subTest(dataset=dataset):
                self.execute(dataset, [(metric, 'count')], user=self.worker, expected=403)
                self.execute(dataset, [(metric, 'count')], include_deleted=True,
                             user=self.viewer, expected=403)
        self.assertNotIn('999', str(self.rows(self.execute(
            'finance_receivables', [('receivable_amount', 'sum')]))))
