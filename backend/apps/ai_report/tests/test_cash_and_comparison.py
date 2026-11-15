from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.test import TestCase
from django.utils import timezone
from openpyxl import load_workbook
from rest_framework.test import APIClient

from apps.accounts.models import Post, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.ai_report.planner import local_plan
from apps.finance.models import CashEntry, Payable, Receivable, ReceiptLine, SupplierPayment
from apps.salary.models import SalaryPayment


class CashDatasetAndComparisonTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username='cash-ai-owner', phone='13900009201',
                                             password='test-only')
        cls.company, _ = create_company_with_owner(cls.owner, '经营报表测试')
        cls.now = timezone.now()
        CashEntry.objects.create(company=cls.company, entry_no='CE-CUR', direction='expense',
                                 category='房租', amount='100.00', occurred_at=cls.now)
        CashEntry.objects.create(company=cls.company, entry_no='CE-OLD', direction='expense',
                                 category='房租', amount='50.00', occurred_at=cls.now-timedelta(days=7))
        CashEntry.objects.create(company=cls.company, entry_no='CE-VOID', direction='expense',
                                 category='房租', amount='999.00', occurred_at=cls.now,
                                 status='voided')
        payable = Payable.objects.create(company=cls.company, payable_no='AP-1',
                                         supplier_name='供应商', amount='200.00',
                                         paid_amount='50.00', status='partial',
                                         payable_at=timezone.localdate())
        SupplierPayment.objects.create(company=cls.company, payable=payable,
                                       supplier_name='供应商', amount='50.00',
                                       paid_at=cls.now)
        Receivable.objects.create(company=cls.company, receivable_no='AR-1',
                                  amount='200.00', received_amount='50.00', status='partial')
        SalaryPayment.objects.create(company=cls.company, employee=cls.owner,
                                     transaction_ref='SAL-1', amount='88.00', paid_at=cls.now)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.client.credentials(HTTP_X_COMPANY_ID=str(self.company.id))

    def execute(self, dataset, metrics, dimensions=None, filters=None, **extra):
        plan = {'dataset': dataset, 'metrics': metrics, 'dimensions': dimensions or [],
                'filters': filters or [], 'order_by': [], 'limit': 100,
                'include_deleted': False, 'presentation': 'table', **extra}
        response = self.client.post('/api/ai/report/execute', {'plan': plan}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    def test_cash_salary_and_payable_datasets_exclude_voided(self):
        cash = self.execute('cash_entries', ['expense_amount', 'other_income_amount',
                                             'other_net_amount'])
        self.assertEqual(
            [Decimal(value) for value in cash['report']['rows'][0][:3]],
            [Decimal('150'), Decimal('0'), Decimal('-150')])
        salary = self.execute('salary_payments', ['payment_count', 'paid_amount'])
        self.assertEqual(salary['report']['rows'][0][0], 1)
        self.assertEqual(Decimal(salary['report']['rows'][0][1]), Decimal('88'))
        payable = self.execute('finance_payables', ['payable_amount', 'paid_amount',
                                                    'outstanding_amount', 'payment_rate'])
        self.assertEqual(
            [Decimal(value) for value in payable['report']['rows'][0]],
            [Decimal('200'), Decimal('50'), Decimal('150'), Decimal('0.25')])
        payment = self.execute('supplier_payments', ['payment_count', 'paid_amount'])
        self.assertEqual(payment['report']['rows'][0][0], 1)
        self.assertEqual(Decimal(payment['report']['rows'][0][1]), Decimal('50'))

    def test_receivable_rate_zero_denominator_is_null(self):
        data = self.execute('finance_receivables', ['collection_rate'])
        self.assertEqual(Decimal(data['report']['rows'][0][0]), Decimal('0.25'))
        Receivable.objects.all().delete()
        empty = self.execute('finance_receivables', ['collection_rate'])
        self.assertEqual(empty['report']['rows'][0], [None])

    def test_previous_period_delta_rate_and_export_sheet(self):
        today = timezone.localdate()
        filters = [{'field': 'occurred_at', 'operator': 'between',
                    'value': [(today-timedelta(days=3)).isoformat(), today.isoformat()]}]
        data = self.execute('cash_entries', ['expense_amount'], filters=filters,
                            comparison='previous')
        comparison = data['report']['comparison']
        self.assertTrue(comparison['available'])
        values = comparison['rows'][0]['metrics']['expense_amount']
        self.assertEqual(Decimal(values['current']), Decimal('100'))
        self.assertEqual(Decimal(values['previous']), Decimal('50'))
        self.assertEqual(Decimal(values['delta']), Decimal('50'))
        self.assertEqual(Decimal(values['change_rate']), Decimal('1'))
        exported = self.client.post('/api/ai/report/export', {'plan': data['plan']}, format='json')
        self.assertEqual(exported.status_code, 200, exported.content)
        workbook = load_workbook(BytesIO(exported.content))
        self.assertIn('期间对比', workbook.sheetnames)

    def test_year_ago_and_truncated_comparison_are_explicit(self):
        today = timezone.localdate()
        try:
            old_day = today.replace(year=today.year - 1)
        except ValueError:
            old_day = today.replace(year=today.year - 1, day=28)
        try:
            old_at = self.now.replace(year=self.now.year - 1)
        except ValueError:
            old_at = self.now.replace(year=self.now.year - 1, day=28)
        CashEntry.objects.create(company=self.company, entry_no='CE-YEAR',
                                 direction='expense', category='房租', amount='25.00',
                                 occurred_at=old_at)
        filters = [{'field': 'occurred_at', 'operator': 'between',
                    'value': [today.isoformat(), today.isoformat()]}]
        data = self.execute('cash_entries', ['expense_amount'], filters=filters,
                            comparison='year_ago')
        comparison = data['report']['comparison']
        self.assertEqual(comparison['previous_range'], [old_day.isoformat(), old_day.isoformat()])
        values = comparison['rows'][0]['metrics']['expense_amount']
        self.assertEqual(
            tuple(Decimal(values[key]) for key in ('current', 'previous', 'change_rate')),
            (Decimal('100'), Decimal('25'), Decimal('3')))
        CashEntry.objects.create(company=self.company, entry_no='CE-CAT',
                                 direction='expense', category='电费', amount='10.00',
                                 occurred_at=self.now)
        limited = self.execute('cash_entries', ['expense_amount'], dimensions=['category'],
                               filters=filters, comparison='previous', limit=1)
        self.assertFalse(limited['report']['comparison']['available'])
        self.assertEqual(limited['report']['comparison']['rows'], [])

    def test_new_finance_questions_use_governed_local_datasets(self):
        cases = {
            '本月各员工实付工资': ('salary_payments', 'paid_amount'),
            '各供应商应付和未付余额': ('finance_payables', 'payable_amount'),
            '各供应商实际付款金额': ('supplier_payments', 'paid_amount'),
            '各类别费用支出': ('cash_entries', 'expense_amount'),
            '应收核销率': ('finance_receivables', 'collection_rate'),
            '应付核销率': ('finance_payables', 'payment_rate'),
            '本月经营现金流': ('finance_cash_flow', 'income'),
            '今年按月经营现金流': ('finance_cash_flow', 'income'),
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                plan = local_plan(question)
                self.assertIsNotNone(plan)
                self.assertEqual(plan['dataset'], expected[0])
                self.assertEqual(plan['metrics'][0]['key'], expected[1])

    def test_unified_cash_flow_combines_five_sources_filters_and_excludes_void(self):
        ReceiptLine.objects.create(
            company=self.company, receivable=Receivable.objects.get(receivable_no='AR-1'),
            amount='200.00', received_at=self.now)
        CashEntry.objects.create(
            company=self.company, entry_no='CE-INCOME', direction='income',
            category='利息', amount='30.00', occurred_at=self.now)
        SalaryPayment.objects.create(
            company=self.company, employee=self.owner, transaction_ref='SAL-VOID',
            amount='999.00', paid_at=self.now, status='void')
        SupplierPayment.objects.create(
            company=self.company, supplier_name='作废付款', amount='999.00',
            paid_at=self.now, status='voided')
        other_owner = User.objects.create_user(
            username='cash-other-owner', phone='13900009202', password='test-only')
        other_company, _ = create_company_with_owner(other_owner, '其他现金流公司')
        CashEntry.objects.create(
            company=other_company, entry_no='CE-OTHER', direction='expense',
            category='其他公司', amount='777.00', occurred_at=self.now)
        today = timezone.localdate().isoformat()
        filters = [{'field': 'occurred_at', 'operator': 'between', 'value': [today, today]}]
        data = self.execute(
            'finance_cash_flow',
            ['customer_receipts', 'other_income', 'salary_payments',
             'supplier_payments', 'expenses', 'net'], filters=filters)
        self.assertEqual(data['report']['rows'][0],
                         ['200', '30', '88', '50', '100', '-8'])
        totals = self.execute(
            'finance_cash_flow', ['income', 'outflow', 'net'], filters=filters)
        self.assertEqual(totals['report']['rows'][0], ['230', '238', '-8'])
        income = self.execute(
            'finance_cash_flow', ['income', 'outflow', 'net'],
            filters=filters + [{'field': 'direction', 'operator': 'eq', 'value': 'income'}])
        self.assertEqual(income['report']['rows'][0], ['230', '0', '230'])
        by_source = self.execute(
            'finance_cash_flow', ['net'], dimensions=['flow_type'], filters=filters,
            order_by=[{'field': 'flow_type', 'direction': 'asc'}])
        self.assertEqual(by_source['report']['total_count'], 5)
        source_values = {row[0]: row[1] for row in by_source['report']['rows']}
        self.assertEqual(source_values, {
            'customer_receipts': '200', 'other_income': '30',
            'salary_payments': '-88', 'supplier_payments': '-50', 'expenses': '-100'})

    def test_unified_cash_flow_month_comparison_and_deleted_guard(self):
        today = timezone.localdate()
        filters = [{'field': 'occurred_at', 'operator': 'between',
                    'value': [(today-timedelta(days=3)).isoformat(), today.isoformat()]}]
        data = self.execute(
            'finance_cash_flow', ['outflow'], dimensions=['occurred_month'],
            filters=filters, comparison='previous')
        self.assertTrue(data['report']['comparison']['available'])
        self.assertEqual(data['report']['comparison']['rows'][0]['metrics']['outflow']['current'],
                         '238')
        response = self.client.post('/api/ai/report/execute', {'plan': {
            'dataset': 'finance_cash_flow', 'metrics': ['net'], 'include_deleted': True,
        }}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('不支持包含作废', str(response.data))

    def test_unified_cash_flow_requires_finance_and_salary_permissions(self):
        post = Post.objects.create(
            company=self.company, name='只有财务资金权限', permissions=['finance.manage'])
        viewer = User.objects.create_user(
            username='cash-finance-only', phone='13900009203', password='test-only')
        UserCompany.objects.create(
            user=viewer, company=self.company, post=post,
            status=UserCompany.Status.ACTIVE)
        client = APIClient()
        client.force_authenticate(viewer)
        client.credentials(HTTP_X_COMPANY_ID=str(self.company.id))
        response = client.post('/api/ai/report/execute', {'plan': {
            'dataset': 'finance_cash_flow', 'metrics': ['net'],
        }}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertIn('无权使用指标', str(response.data))
        catalog = client.get('/api/ai/report/catalog')
        self.assertNotIn('finance_cash_flow', {
            item['key'] for item in catalog.data['datasets']})
        post.permissions = ['finance.manage', 'salary.view']
        post.save(update_fields=['permissions'])
        allowed = client.post('/api/ai/report/execute', {'plan': {
            'dataset': 'finance_cash_flow', 'metrics': ['net'],
        }}, format='json')
        self.assertEqual(allowed.status_code, 200, allowed.content)

    def test_comparison_requires_main_date_range(self):
        response = self.client.post('/api/ai/report/execute', {'plan': {
            'dataset': 'cash_entries', 'metrics': ['expense_amount'], 'dimensions': [],
            'filters': [], 'comparison': 'previous'}}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('主时间字段', str(response.data))
