# -*- coding: utf-8 -*-
"""动态 QueryPlan 第一批纵向闭环测试；所有用例使用隔离测试数据库。"""
import json
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.utils import timezone
from django.test import TestCase, override_settings
from openpyxl import load_workbook
from rest_framework.test import APIClient

from apps.accounts.models import Post, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.ai_report.models import AiQueryExecution, AiQueryLog
from apps.ai_report.planner import local_revision
from apps.catalog.models import Customer, Process, Style, Ticket
from apps.orders.models import Order, OrderItem
from apps.production.models import Production
from apps.records.models import Record


class QueryPlanApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.now = timezone.now()
        cls.today = timezone.localdate().isoformat()
        cls.yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
        cls.owner = User.objects.create_user(
            username='qp-owner', phone='13900008101', password='test-only',
            real_name='动态报表厂长')
        cls.company, admin_post = create_company_with_owner(cls.owner, '动态报表测试A厂')
        owner_link = UserCompany.objects.get(user=cls.owner, company=cls.company)
        owner_link.group_no = 1
        owner_link.save(update_fields=['group_no', 'updated_at'])

        plain_post = Post.objects.create(company=cls.company, name='动态普工', permissions=[])
        analyst_post = Post.objects.create(
            company=cls.company, name='动态分析员',
            permissions=['salary.view', 'ai.export'])
        cls.worker = User.objects.create_user(
            username='qp-worker', phone='13900008102', password='test-only', real_name='张三')
        cls.analyst = User.objects.create_user(
            username='qp-analyst', phone='13900008103', password='test-only', real_name='分析员')
        UserCompany.objects.create(
            user=cls.worker, company=cls.company, post=plain_post,
            status=UserCompany.Status.ACTIVE, group_no=2)
        UserCompany.objects.create(
            user=cls.analyst, company=cls.company, post=analyst_post,
            status=UserCompany.Status.ACTIVE, group_no=3)

        customer = Customer.objects.create(
            company=cls.company, customer_no='QP-C1', name='动态客户')
        style = Style.objects.create(
            company=cls.company, style_no='QP-S1', name='动态款', customer=customer)
        cls.delivery_date = (timezone.localdate() + timedelta(days=7)).isoformat()
        cls.order = Order.objects.create(
            company=cls.company, order_no='QP-O1', style=style, customer=customer,
            delivery_date=cls.delivery_date)
        OrderItem.objects.bulk_create([
            OrderItem(order=cls.order, color='黑色', size='M', qty=120),
            OrderItem(order=cls.order, color='=1+1', size='L', qty=80),
        ])
        Order.objects.create(
            company=cls.company, order_no='QP-O-DELETED', style=style,
            customer=customer, is_deleted=True)
        cls.production = Production.objects.create(
            company=cls.company, style=style, customer=customer, order=cls.order,
            bed_no='QP-B1', total_qty=200, plan_qty=180)
        cls.ticket1 = Ticket.objects.create(
            company=cls.company, production=cls.production, bed_no='QP-B1', bundle_no='1',
            color='黑色', size='M', qty=100, qr_code='QP-QR-1')
        cls.ticket2 = Ticket.objects.create(
            company=cls.company, production=cls.production, bed_no='QP-B1', bundle_no='2',
            color='=1+1', size='L', qty=100, qr_code='QP-QR-2')
        sew = Process.objects.create(company=cls.company, name='QP-车缝')
        pack = Process.objects.create(company=cls.company, name='QP-包装')
        Record.objects.bulk_create([
            Record(company=cls.company, ticket=cls.ticket1, process=sew, user=cls.owner,
                   qty=10, price='2.000', registered_at=cls.now),
            Record(company=cls.company, ticket=cls.ticket1, process=sew, user=cls.worker,
                   qty=5, price='3.000', registered_at=cls.now),
            Record(company=cls.company, ticket=cls.ticket1, process=pack, user=cls.worker,
                   qty=7, price='1.000', registered_at=cls.now - timedelta(days=1)),
            Record(company=cls.company, ticket=cls.ticket1, process=pack, user=cls.owner,
                   qty=100, price='10.000', registered_at=cls.now, is_deleted=True),
            Record(company=cls.company, ticket=cls.ticket2, process=sew, user=cls.owner,
                   qty=2, price='1.000', registered_at=cls.now),
        ])

        other_owner = User.objects.create_user(
            username='qp-other-owner', phone='13900008104', password='test-only')
        other_company, _ = create_company_with_owner(other_owner, '动态报表测试B厂')
        other_style = Style.objects.create(company=other_company, style_no='QP-B-S1')
        other_order = Order.objects.create(
            company=other_company, order_no='QP-B-O1', style=other_style)
        OrderItem.objects.create(
            order=other_order, color='他厂颜色', size='M', qty=999)
        other_production = Production.objects.create(
            company=other_company, style=other_style, order=other_order,
            bed_no='QP-B-B1', total_qty=999)
        other_ticket = Ticket.objects.create(
            company=other_company, production=other_production,
            bed_no='QP-B-B1', bundle_no='1', color='他厂颜色', size='M',
            qty=999, qr_code='QP-OTHER-QR')
        other_process = Process.objects.create(company=other_company, name='QP-他厂工序')
        Record.objects.create(
            company=other_company, ticket=other_ticket, process=other_process,
            user=other_owner, qty=999, price='99.000', registered_at=cls.now)

    def client_for(self, user, company=None):
        client = APIClient()
        client.force_authenticate(user)
        client.credentials(HTTP_X_COMPANY_ID=str((company or self.company).id))
        return client

    def execute(self, user, plan, expected=200):
        response = self.client_for(user).post('/api/ai/report/execute', {'plan': plan}, format='json')
        self.assertEqual(response.status_code, expected, response.content)
        return response

    @staticmethod
    def base_plan(**patch):
        plan = {
            'dataset': 'piecework_records',
            'metrics': [{'key': 'registered_qty', 'aggregation': 'sum'}],
            'dimensions': [],
            'filters': [],
            'order_by': [],
            'limit': 100,
            'include_deleted': False,
            'presentation': 'table',
        }
        plan.update(patch)
        return plan

    @staticmethod
    def report_rows(response):
        report = response.data['report']
        keys = [column['key'] for column in report['columns']]
        return [dict(zip(keys, row)) for row in report['rows']]

    def test_admin_process_aggregation_has_exact_values(self):
        plan = self.base_plan(
            metrics=[
                {'key': 'registered_qty', 'aggregation': 'sum'},
                {'key': 'estimated_wage', 'aggregation': 'sum'},
                {'key': 'record_count', 'aggregation': 'count'},
                {'key': 'employee_count', 'aggregation': 'count_distinct'},
                {'key': 'ticket_count', 'aggregation': 'count_distinct'},
            ],
            dimensions=['process_name'],
            filters=[{'field': 'registered_at', 'operator': 'between',
                      'value': [self.today, self.today]}],
        )
        response = self.execute(self.owner, plan)
        rows = self.report_rows(response)
        self.assertEqual(len(rows), 1)
        self.assertEqual(Decimal(rows[0].pop('estimated_wage')), Decimal('37'))
        self.assertEqual(rows[0], {
            'process_name': 'QP-车缝', 'registered_qty': 17,
            'record_count': 3, 'employee_count': 2, 'ticket_count': 2,
        })
        self.assertEqual(response.data['mode'], 'query_plan')
        self.assertTrue(response.data['execution_id'])
        self.assertTrue(any('排除已撤销登记' in item for item in response.data['report']['scope']))

    def test_group_dimension_and_arbitrary_date_filter(self):
        response = self.execute(self.owner, self.base_plan(
            dimensions=['group_no'],
            order_by=[{'field': 'group_no', 'direction': 'asc'}],
        ))
        self.assertEqual(self.report_rows(response), [
            {'group_no': 1, 'registered_qty': 12},
            {'group_no': 2, 'registered_qty': 12},
        ])
        response = self.execute(self.owner, self.base_plan(
            dimensions=['group_no'],
            filters=[{'field': 'registered_at', 'operator': 'between',
                      'value': [self.yesterday, self.yesterday]}],
        ))
        self.assertEqual(self.report_rows(response), [
            {'group_no': 2, 'registered_qty': 7},
        ])

    def test_quantity_aggregations_and_calendar_dimensions(self):
        expected = {'sum': 24, 'avg': '6.0', 'min': 2, 'max': 10}
        for aggregation, value in expected.items():
            with self.subTest(aggregation=aggregation):
                response = self.execute(self.owner, self.base_plan(
                    metrics=[{'key': 'registered_qty', 'aggregation': aggregation}]))
                actual = self.report_rows(response)[0]['registered_qty']
                if aggregation == 'avg':
                    self.assertAlmostEqual(float(actual), 6.0)
                else:
                    self.assertEqual(actual, value)

        for dimension in ('registered_day', 'registered_week', 'registered_month'):
            with self.subTest(dimension=dimension):
                response = self.execute(self.owner, self.base_plan(dimensions=[dimension]))
                rows = self.report_rows(response)
                self.assertEqual(sum(row['registered_qty'] for row in rows), 24)
                self.assertTrue(all(row[dimension] for row in rows))

    def test_new_endpoints_require_authentication_and_company_context(self):
        anonymous = APIClient()
        response = anonymous.post('/api/ai/report/execute', {'plan': self.base_plan()}, format='json')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(anonymous.get('/api/ai/report/catalog').status_code, 401)

        missing_company = APIClient()
        missing_company.force_authenticate(self.owner)
        self.assertEqual(missing_company.get('/api/ai/report/catalog').status_code, 400)
        self.execute(self.owner, self.base_plan(dataset='unknown_dataset'), expected=400)

    def test_export_has_an_independent_row_limit(self):
        response = self.client_for(self.owner).post(
            '/api/ai/report/export', {'plan': self.base_plan(limit=501)}, format='json')
        self.assertEqual(response.status_code, 200)
        response = self.client_for(self.owner).post(
            '/api/ai/report/export', {'plan': self.base_plan(limit=5001)}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_soft_deleted_rows_require_full_data_permission(self):
        plan = self.base_plan(dimensions=['process_name'])
        default_rows = self.report_rows(self.execute(self.owner, plan))
        pack = next(row for row in default_rows if row['process_name'] == 'QP-包装')
        self.assertEqual(pack['registered_qty'], 7)

        plan['include_deleted'] = True
        included_rows = self.report_rows(self.execute(self.owner, plan))
        pack = next(row for row in included_rows if row['process_name'] == 'QP-包装')
        self.assertEqual(pack['registered_qty'], 107)
        self.execute(self.analyst, plan, expected=403)

    def test_catalog_and_sensitive_fields_follow_permissions(self):
        plain_catalog = self.client_for(self.worker).get('/api/ai/report/catalog')
        self.assertEqual(plain_catalog.status_code, 200)
        dataset = plain_catalog.data['datasets'][0]
        self.assertNotIn('estimated_wage', {item['key'] for item in dataset['metrics']})
        self.assertNotIn('employee_name', {item['key'] for item in dataset['fields']})
        self.assertFalse(plain_catalog.data['capabilities']['export'])

        self.execute(self.worker, self.base_plan())
        self.execute(self.worker, self.base_plan(
            metrics=[{'key': 'estimated_wage', 'aggregation': 'sum'}]), expected=403)
        self.execute(self.worker, self.base_plan(dimensions=['employee_name']), expected=403)
        self.execute(self.worker, self.base_plan(include_deleted=True), expected=403)

        analyst_catalog = self.client_for(self.analyst).get('/api/ai/report/catalog')
        self.assertIn('estimated_wage', {
            item['key'] for item in analyst_catalog.data['datasets'][0]['metrics']})
        self.assertFalse(analyst_catalog.data['capabilities']['full_data'])
        self.assertTrue(analyst_catalog.data['capabilities']['export'])

    def test_chart_uses_same_governed_plan_and_result(self):
        response = self.execute(self.owner, self.base_plan(
            dimensions=['color'], presentation='bar'))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['plan']['presentation'], 'bar')
        self.assertTrue(response.data['report']['rows'])
        response = self.execute(self.owner, self.base_plan(
            dimensions=['registered_day'],
            order_by=[{'field': 'registered_day', 'direction': 'asc'}],
            presentation='line'))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['plan']['presentation'], 'line')
        for invalid in (
            self.base_plan(presentation='bar'),
            self.base_plan(dimensions=['color'], presentation='bar', metrics=[
                {'key': 'registered_qty', 'aggregation': 'sum'},
                {'key': 'record_count', 'aggregation': 'count'},
            ]),
            self.base_plan(dimensions=['color'], presentation='line'),
        ):
            with self.subTest(invalid=invalid):
                self.execute(self.owner, invalid, expected=400)

    def test_invalid_plan_is_rejected_instead_of_guessing(self):
        invalid_plans = [
            self.base_plan(company_id=999999),
            self.base_plan(dimensions=['user__username']),
            self.base_plan(filters=[{'field': 'registered_at', 'operator': 'between',
                                     'value': ['not-a-date', self.today]}]),
            self.base_plan(filters=[{'field': 'registered_at', 'operator': 'between',
                                     'value': [self.today, self.yesterday]}]),
            self.base_plan(order_by=[{'field': 'record_count', 'direction': 'desc'}]),
            self.base_plan(limit=501),
            self.base_plan(metrics=[]),
            self.base_plan(metrics=[{'key': 'employee_count', 'aggregation': 'sum'}]),
            self.base_plan(filters=[{'field': 'process_name', 'operator': 'regex', 'value': 'x'}]),
            self.base_plan(presentation='chart'),
            self.base_plan(dimensions=[{'key': 'process_name'}]),
            self.base_plan(metrics=[{'key': {'nested': 'registered_qty'}}]),
            self.base_plan(filters=[{'field': {'nested': 'color'}, 'operator': 'eq', 'value': '黑'}]),
            self.base_plan(order_by=[{'field': {'nested': 'registered_qty'}, 'direction': 'desc'}]),
        ]
        for plan in invalid_plans:
            with self.subTest(plan=plan):
                self.execute(self.owner, plan, expected=400)

    def test_company_scope_is_forced_and_filter_values_are_parameterized(self):
        response = self.execute(self.owner, self.base_plan())
        self.assertEqual(self.report_rows(response), [{'registered_qty': 24}])

        response = self.execute(self.owner, self.base_plan(
            dimensions=['color'],
            filters=[{'field': 'color', 'operator': 'eq', 'value': "x' OR 1=1 --"}],
        ))
        self.assertEqual(response.data['report']['total_count'], 0)
        self.assertEqual(response.data['report']['rows'], [])

    def test_limit_returns_total_count_and_truncation(self):
        response = self.execute(self.owner, self.base_plan(
            dimensions=['process_name'], limit=1,
            order_by=[{'field': 'process_name', 'direction': 'asc'}],
        ))
        report = response.data['report']
        self.assertEqual(report['total_count'], 2)
        self.assertEqual(report['returned_count'], 1)
        self.assertTrue(report['truncated'])
        execution = AiQueryExecution.objects.get(id=response.data['execution_id'])
        self.assertTrue(execution.truncated)
        self.assertEqual(execution.total_count, 2)

    def test_export_uses_same_values_and_neutralizes_formulas(self):
        response = self.client_for(self.owner).post(
            '/api/ai/report/export',
            {'plan': self.base_plan(dimensions=['color'],
                                    order_by=[{'field': 'color', 'direction': 'asc'}])},
            format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.content[:2], b'PK')
        self.assertTrue(response['X-Ai-Execution-Id'])
        book = load_workbook(BytesIO(response.content), data_only=False)
        values = list(book.active.values)
        self.assertEqual(values[0], ('颜色', '登记件数'))
        formula_cell = next(
            row[0] for row in book.active.iter_rows() if row[0].value == "'=1+1")
        self.assertNotEqual(formula_cell.data_type, 'f')

        denied = self.client_for(self.worker).post(
            '/api/ai/report/export', {'plan': self.base_plan()}, format='json')
        self.assertEqual(denied.status_code, 403)
        analyst = self.client_for(self.analyst).post(
            '/api/ai/report/export',
            {'plan': self.base_plan(metrics=[{'key': 'estimated_wage', 'aggregation': 'sum'}])},
            format='json')
        self.assertEqual(analyst.status_code, 200)

    def test_order_dataset_preaggregates_related_rows_without_fanout(self):
        response = self.execute(self.owner, self.base_plan(
            dataset='orders',
            metrics=[
                {'key': 'order_count', 'aggregation': 'count'},
                {'key': 'order_qty', 'aggregation': 'sum'},
                {'key': 'cut_qty', 'aggregation': 'sum'},
                {'key': 'registered_qty', 'aggregation': 'sum'},
            ],
            dimensions=['status'],
            filters=[{'field': 'delivery_date', 'operator': 'between',
                      'value': [self.today, self.delivery_date]}],
        ))
        self.assertEqual(self.report_rows(response), [{
            'status': 'producing', 'order_count': 1, 'order_qty': 200,
            'cut_qty': 200, 'registered_qty': 24,
        }])
        status_column = response.data['report']['columns'][0]
        self.assertIn({'value': 'producing', 'label': '生产中'}, status_column['choices'])
        self.assertTrue(any('排除已删除订单' in item
                            for item in response.data['report']['scope']))
        exported = self.client_for(self.owner).post(
            '/api/ai/report/export', {'plan': response.data['plan']}, format='json')
        self.assertEqual(exported.status_code, 200, exported.content)
        book = load_workbook(BytesIO(exported.content), data_only=False)
        self.assertEqual(book.active.cell(row=2, column=1).value, '生产中')

    def test_order_dataset_soft_delete_and_choice_validation(self):
        plan = self.base_plan(
            dataset='orders', metrics=[{'key': 'order_count', 'aggregation': 'count'}],
            filters=[{'field': 'status', 'operator': 'eq', 'value': 'producing'}])
        self.assertEqual(self.report_rows(self.execute(self.owner, plan)), [{
            'order_count': 1,
        }])
        plan['include_deleted'] = True
        self.assertEqual(self.report_rows(self.execute(self.owner, plan)), [{
            'order_count': 2,
        }])
        plan['filters'] = [{'field': 'delivery_date', 'operator': 'isnull', 'value': True}]
        self.assertEqual(self.report_rows(self.execute(self.owner, plan)), [{
            'order_count': 1,
        }])
        plan['include_deleted'] = False
        self.assertEqual(self.report_rows(self.execute(self.owner, plan)), [{
            'order_count': 0,
        }])
        plan['filters'] = [{'field': 'status', 'operator': 'eq', 'value': 'not-a-status'}]
        self.execute(self.owner, plan, expected=400)

    def test_production_dataset_preaggregates_records_and_tickets(self):
        response = self.execute(self.owner, self.base_plan(
            dataset='productions',
            metrics=[
                {'key': 'production_count', 'aggregation': 'count'},
                {'key': 'cut_qty', 'aggregation': 'sum'},
                {'key': 'plan_qty', 'aggregation': 'sum'},
                {'key': 'registered_qty', 'aggregation': 'sum'},
                {'key': 'ticket_count', 'aggregation': 'count'},
            ],
            dimensions=['is_cut_done'],
            filters=[{'field': 'is_locked', 'operator': 'eq', 'value': False}],
        ))
        self.assertEqual(self.report_rows(response), [{
            'is_cut_done': False, 'production_count': 1, 'cut_qty': 200,
            'plan_qty': 180, 'registered_qty': 24, 'ticket_count': 2,
        }])

    def test_dataset_permissions_apply_to_catalog_and_direct_execution(self):
        owner_keys = {item['key'] for item in
                      self.client_for(self.owner).get('/api/ai/report/catalog').data['datasets']}
        self.assertEqual(owner_keys, {
            'piecework_records', 'orders', 'productions', 'shipments', 'warehouse_lots',
            'material_stock', 'purchase_items', 'salary_settlements', 'salary_applications',
            'salary_payments', 'finance_receivables', 'finance_receipts', 'finance_payables',
            'supplier_payments', 'cash_entries', 'finance_cash_flow',
        })

        worker_catalog = self.client_for(self.worker).get('/api/ai/report/catalog')
        self.assertEqual(
            {item['key'] for item in worker_catalog.data['datasets']},
            {'piecework_records'})
        for dataset, metric in (('orders', 'order_count'), ('productions', 'production_count')):
            with self.subTest(dataset=dataset):
                self.execute(self.worker, self.base_plan(
                    dataset=dataset,
                    metrics=[{'key': metric, 'aggregation': 'count'}]), expected=403)

    def ask(self, user, question, history=None, expected=200):
        response = self.client_for(user).post('/api/ai/report/query', {
            'question': question, 'history': history or [], 'mode': 'query_plan',
        }, format='json')
        self.assertEqual(response.status_code, expected, response.content)
        return response

    def test_natural_local_planner_executes_piecework_and_wage_plans(self):
        response = self.ask(self.owner, '今天各工序登记多少件？')
        self.assertEqual(response.data['planner']['source'], 'local')
        self.assertEqual(self.report_rows(response), [{
            'process_name': 'QP-车缝', 'registered_qty': 17,
        }])
        self.assertEqual(response.data['plan']['filters'][0]['field'], 'registered_at')

        response = self.ask(self.owner, '今天各员工的计件工资是多少？')
        rows = self.report_rows(response)
        self.assertEqual([(row['employee_name'], Decimal(row['estimated_wage']))
                          for row in rows], [
            ('动态报表厂长', Decimal('22')), ('张三', Decimal('15')),
        ])
        query_log = AiQueryLog.objects.get(question='今天各员工的计件工资是多少？')
        self.assertTrue(query_log.ok)
        self.assertEqual(query_log.intent['planner']['source'], 'local')
        self.assertEqual(query_log.intent['query_plan']['dataset'], 'piecework_records')

    def test_natural_local_planner_supports_orders_and_enforces_dataset_permission(self):
        response = self.ask(self.owner, '各客户订单数量和登记量')
        self.assertEqual(self.report_rows(response), [{
            'customer_name': '动态客户', 'order_qty': 200, 'registered_qty': 24,
        }])
        self.assertEqual(response.data['plan']['filters'], [])
        self.assertEqual(response.data['mode'], 'query_plan')

        denied = self.ask(self.worker, '各客户订单数量和登记量', expected=403)
        self.assertIn('无权查询数据集', str(denied.data))
        log = AiQueryExecution.objects.latest('id')
        self.assertEqual(log.status, 'denied')
        self.assertFalse(AiQueryLog.objects.latest('id').ok)

    def test_local_followup_can_cancel_and_replace_explicit_filters(self):
        previous = self.base_plan(filters=[
            {'field': 'customer_name', 'operator': 'contains', 'value': '原客户'},
            {'field': 'style_no', 'operator': 'eq', 'value': 'OLD-1'},
        ])
        replaced = local_revision('把客户改为新客户', previous)
        self.assertEqual(replaced['filters'][0], {
            'field': 'customer_name', 'operator': 'contains', 'value': '新客户'})
        self.assertEqual(previous['filters'][0]['value'], '原客户')
        cancelled = local_revision('取消客户筛选', replaced)
        self.assertEqual(cancelled['filters'], [
            {'field': 'style_no', 'operator': 'eq', 'value': 'OLD-1'}])
        cleared = local_revision('清空筛选条件', previous)
        self.assertEqual(cleared['filters'], [])
        self.assertIsNone(local_revision('随便改一下', previous))

    @override_settings(GLM_API_KEY='test-only-key', AI_FREE_MODE=False)
    def test_natural_glm_planner_validates_plan_and_only_receives_catalog(self):
        candidate = self.base_plan(
            dimensions=['color'],
            filters=[
                {'field': 'color', 'operator': 'eq', 'value': '黑色'},
                {'field': 'size', 'operator': 'eq', 'value': 'M'},
            ],
            order_by=[{'field': 'registered_qty', 'direction': 'desc'}],
        )
        history = [
            {'role': 'user', 'content': '上一轮只看有效登记'},
            {'role': 'ai', 'content': '已按条件生成报表'},
        ]
        with patch('apps.ai_report.planner.services._glm_chat',
                   return_value='```json\n' + json.dumps({'plan': candidate}) + '\n```') as chat:
            response = self.ask(self.owner, '按颜色统计黑色M码的情况', history=history)
        self.assertEqual(self.report_rows(response), [{
            'color': '黑色', 'registered_qty': 22,
        }])
        self.assertEqual(response.data['planner']['source'], 'glm')
        self.assertEqual(len(response.data['plan']['filters']), 2)
        messages = chat.call_args.args[0]
        self.assertEqual(messages[0]['role'], 'system')
        self.assertEqual(messages[1]['content'], '上一轮只看有效登记')
        self.assertEqual(messages[2]['role'], 'assistant')
        for business_value in ('QP-O1', '动态客户', '张三', 'QP-QR-1'):
            self.assertNotIn(business_value, messages[0]['content'])

    @override_settings(GLM_API_KEY='test-only-key', AI_FREE_MODE=False)
    def test_natural_followup_receives_previous_server_side_plan(self):
        self.ask(self.owner, '今天各工序登记多少件？')
        candidate = self.base_plan(
            dimensions=['process_name'],
            filters=[{'field': 'registered_at', 'operator': 'between',
                      'value': [self.yesterday, self.yesterday]}],
            order_by=[{'field': 'registered_qty', 'direction': 'desc'}],
        )
        with patch('apps.ai_report.planner.services._glm_chat',
                   return_value=json.dumps({'plan': candidate})) as chat:
            response = self.ask(self.owner, '那昨天呢？')
        prompt = chat.call_args.args[0][0]['content']
        self.assertIn('上一轮由服务端执行成功的规范化计划', prompt)
        self.assertIn(self.today, prompt)
        self.assertEqual(self.report_rows(response), [{
            'process_name': 'QP-包装', 'registered_qty': 7,
        }])
        self.assertEqual(response.data['plan']['filters'][0]['value'], [
            self.yesterday, self.yesterday,
        ])

    @override_settings(GLM_API_KEY='', AI_FREE_MODE=True)
    def test_natural_planner_without_key_preserves_safe_fallback(self):
        with patch('apps.ai_report.views.services.free_answer') as free_answer:
            response = self.ask(self.owner, '随便聊聊报表以外的话题')
        self.assertTrue(response.data['fallback'])
        self.assertIn('quick_questions', response.data)
        self.assertNotIn('report', response.data)
        free_answer.assert_not_called()

    @override_settings(GLM_API_KEY='test-only-key', AI_FREE_MODE=False)
    def test_natural_glm_bad_json_and_transport_errors_fall_back(self):
        for effect in ('not-json', TimeoutError('planner timeout')):
            with self.subTest(effect=effect):
                with patch('apps.ai_report.planner.services._glm_chat',
                           **({'return_value': effect} if isinstance(effect, str)
                              else {'side_effect': effect})), patch(
                                  'apps.ai_report.views.services.parse_intent', return_value=None):
                    response = self.ask(self.owner, '随便聊聊报表以外的话题')
                self.assertEqual(response.status_code, 200, response.content)
                self.assertTrue(response.data['fallback'])

    @override_settings(GLM_API_KEY='test-only-key', AI_FREE_MODE=False)
    def test_natural_glm_cannot_override_company_or_inject_sql(self):
        invalid_plans = [
            self.base_plan(company_id=999999),
            self.base_plan(sql='DELETE FROM records_record'),
            self.base_plan(dimensions=['user__username']),
        ]
        for candidate in invalid_plans:
            with self.subTest(candidate=candidate):
                with patch('apps.ai_report.planner.services._glm_chat',
                           return_value=json.dumps({'plan': candidate})):
                    self.ask(self.owner, '一个需要模型解析的自定义条件', expected=400)
                self.assertEqual(AiQueryExecution.objects.latest('id').status, 'invalid')

    @override_settings(GLM_API_KEY='test-only-key', AI_FREE_MODE=False)
    def test_natural_glm_sensitive_field_is_rechecked_server_side(self):
        candidate = self.base_plan(
            metrics=[{'key': 'estimated_wage', 'aggregation': 'sum'}],
            dimensions=['employee_name'])
        with patch('apps.ai_report.planner.services._glm_chat',
                   return_value=json.dumps(candidate)) as chat:
            self.ask(self.worker, '一个需要模型解析的自定义条件', expected=403)
        prompt = chat.call_args.args[0][0]['content']
        self.assertNotIn("'key': 'employee_name'", prompt)
        self.assertNotIn('"key": "employee_name"', prompt)
        self.assertEqual(AiQueryExecution.objects.latest('id').status, 'denied')

    def test_execution_audit_covers_success_invalid_denied_and_export(self):
        success = self.execute(self.owner, self.base_plan())
        execution = AiQueryExecution.objects.get(id=success.data['execution_id'])
        self.assertEqual(execution.status, 'success')
        self.assertEqual(execution.company, self.company)
        self.assertEqual(execution.plan['dataset'], 'piecework_records')
        self.assertEqual(execution.row_count, 1)
        self.assertGreater(execution.sql_count, 0)
        self.assertEqual(execution.sql_count, success.data['performance']['sql_count'])
        self.assertEqual(execution.sql_time_ms, success.data['performance']['sql_time_ms'])

        self.execute(self.owner, self.base_plan(limit=0), expected=400)
        self.execute(self.worker, self.base_plan(
            metrics=[{'key': 'estimated_wage', 'aggregation': 'sum'}]), expected=403)
        exported = self.client_for(self.owner).post(
            '/api/ai/report/export', {'plan': self.base_plan()}, format='json')
        self.assertEqual(exported.status_code, 200)

        logs = list(AiQueryExecution.objects.order_by('id'))
        self.assertEqual([log.status for log in logs], ['success', 'invalid', 'denied', 'success'])
        self.assertEqual(logs[-1].mode, 'export')
        self.assertEqual(logs[1].error_code, 'invalid_plan')
        self.assertEqual(logs[2].error_code, 'permission_denied')

    @override_settings(AI_REPORT_SLOW_MS=0)
    def test_slow_query_metadata_does_not_log_filters_or_results(self):
        secret = '慢查询机密筛选值'
        with self.assertLogs('apps.ai_report.plan_views', level='WARNING') as logs:
            response = self.execute(self.owner, self.base_plan(filters=[{
                'field': 'color', 'operator': 'contains', 'value': secret,
            }]))
        self.assertTrue(response.data['performance']['slow'])
        self.assertTrue(AiQueryExecution.objects.latest('id').slow)
        self.assertNotIn(secret, '\n'.join(logs.output))
        self.assertNotIn('SELECT', '\n'.join(logs.output))
