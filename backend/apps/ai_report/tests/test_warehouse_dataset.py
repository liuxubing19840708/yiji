# -*- coding: utf-8 -*-
"""仓库批次流水库存：认证口径、单位防混加、权限和公司隔离。"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Post, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Style
from apps.production.models import Production
from apps.warehouse.models import (
    IssueItem, Lot, MaterialIssue, StockIn, StockInItem,
)


class WarehouseLotDatasetApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username='warehouse-report-owner', phone='13900008401', password='test-only')
        cls.company, _ = create_company_with_owner(cls.owner, '仓库报表A厂')
        warehouse_post = Post.objects.create(
            company=cls.company, name='仓库报表岗', permissions=['warehouse.manage'])
        plain_post = Post.objects.create(company=cls.company, name='仓库报表普工', permissions=[])
        cls.viewer = User.objects.create_user(
            username='warehouse-report-viewer', phone='13900008402', password='test-only')
        cls.worker = User.objects.create_user(
            username='warehouse-report-worker', phone='13900008403', password='test-only')
        UserCompany.objects.create(
            user=cls.viewer, company=cls.company, post=warehouse_post,
            status=UserCompany.Status.ACTIVE)
        UserCompany.objects.create(
            user=cls.worker, company=cls.company, post=plain_post,
            status=UserCompany.Status.ACTIVE)
        style = Style.objects.create(
            company=cls.company, style_no='WH-S1', name='仓库报表款')
        production = Production.objects.create(
            company=cls.company, style=style, bed_no='WH-B1', total_qty=100)
        cls.lot1 = Lot.objects.create(
            company=cls.company, lot_no='WH-L1', fabric_name='棉布', color='黑色',
            qty=999, unit='米')
        cls.lot2 = Lot.objects.create(
            company=cls.company, lot_no='WH-L2', fabric_name='棉布', color='白色',
            qty=888, unit='米')
        cls.lot3 = Lot.objects.create(
            company=cls.company, lot_no='WH-L3', fabric_name='羊毛', color='灰色',
            qty=777, unit='公斤')
        cls.deleted_lot = Lot.objects.create(
            company=cls.company, lot_no='WH-L-DEL', fabric_name='棉布', unit='米',
            qty=500, is_deleted=True)

        def stock_in(number, status=StockIn.Status.DONE, deleted=False):
            return StockIn.objects.create(
                company=cls.company, in_no=number, in_at=timezone.now(),
                status=status, is_deleted=deleted)

        done_in = stock_in('WH-IN-1')
        draft_in = stock_in('WH-IN-DRAFT', StockIn.Status.DRAFT)
        deleted_in = stock_in('WH-IN-DELETED', deleted=True)
        StockInItem.objects.bulk_create([
            StockInItem(stock_in=done_in, lot=cls.lot1, fabric_name='棉布',
                        lot_no='WH-L1', qty='100.00', unit='米'),
            StockInItem(stock_in=done_in, lot=cls.lot2, fabric_name='棉布',
                        lot_no='WH-L2', qty='50.00', unit='米'),
            StockInItem(stock_in=done_in, lot=cls.lot3, fabric_name='羊毛',
                        lot_no='WH-L3', qty='20.00', unit='公斤'),
            StockInItem(stock_in=done_in, lot=cls.deleted_lot, fabric_name='棉布',
                        lot_no='WH-L-DEL', qty='500.00', unit='米'),
            StockInItem(stock_in=draft_in, lot=cls.lot1, fabric_name='棉布',
                        lot_no='WH-L1', qty='10.00', unit='米'),
            StockInItem(stock_in=draft_in, lot=cls.lot2, fabric_name='棉布',
                        lot_no='WH-L2', qty='5.00', unit='米'),
            StockInItem(stock_in=deleted_in, lot=cls.lot1, fabric_name='棉布',
                        lot_no='WH-L1', qty='900.00', unit='米'),
            StockInItem(stock_in=done_in, lot=cls.lot2, fabric_name='棉布',
                        lot_no='WH-L2', qty='800.00', unit='米', is_deleted=True),
        ])

        def issue(number, issue_type, status=MaterialIssue.Status.CONFIRMED):
            return MaterialIssue.objects.create(
                company=cls.company, issue_no=number, type=issue_type,
                status=status, issue_at=timezone.now())

        issue1 = issue('WH-ISSUE-1', MaterialIssue.Type.ISSUE)
        issue2 = issue('WH-ISSUE-2', MaterialIssue.Type.ISSUE)
        returned = issue('WH-RETURN-1', MaterialIssue.Type.RETURN)
        draft_issue = issue('WH-ISSUE-DRAFT', MaterialIssue.Type.ISSUE,
                            MaterialIssue.Status.DRAFT)
        IssueItem.objects.bulk_create([
            IssueItem(issue=issue1, production=production, lot=cls.lot1, qty='30.00'),
            IssueItem(issue=issue1, production=production, lot=cls.lot2, qty='5.00'),
            IssueItem(issue=issue1, production=production, lot=cls.lot3, qty='2.00'),
            IssueItem(issue=issue2, production=production, lot=cls.lot1, qty='10.00'),
            IssueItem(issue=returned, production=production, lot=cls.lot1, qty='4.00'),
            IssueItem(issue=draft_issue, production=production, lot=cls.lot1, qty='200.00'),
        ])

        other_owner = User.objects.create_user(
            username='warehouse-report-other', phone='13900008404', password='test-only')
        other_company, _ = create_company_with_owner(other_owner, '仓库报表B厂')
        other_lot = Lot.objects.create(
            company=other_company, lot_no='WH-OTHER', fabric_name='他厂面料', unit='米')
        other_in = StockIn.objects.create(
            company=other_company, in_no='WH-OTHER-IN', in_at=timezone.now(),
            status=StockIn.Status.DONE)
        StockInItem.objects.create(
            stock_in=other_in, lot=other_lot, fabric_name='他厂面料',
            lot_no='WH-OTHER', qty='999.00', unit='米')

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        client.credentials(HTTP_X_COMPANY_ID=str(self.company.id))
        return client

    @staticmethod
    def plan(**patch):
        value = {
            'dataset': 'warehouse_lots',
            'metrics': [
                {'key': 'lot_count', 'aggregation': 'count'},
                {'key': 'inbound_qty', 'aggregation': 'sum'},
                {'key': 'issued_qty', 'aggregation': 'sum'},
                {'key': 'returned_qty', 'aggregation': 'sum'},
                {'key': 'current_stock_qty', 'aggregation': 'sum'},
            ],
            'dimensions': ['lot_no', 'unit'],
            'filters': [],
            'order_by': [{'field': 'lot_no', 'direction': 'asc'}],
            'limit': 100,
            'include_deleted': False,
            'presentation': 'table',
        }
        value.update(patch)
        return value

    @staticmethod
    def rows(response):
        keys = [column['key'] for column in response.data['report']['columns']]
        return [dict(zip(keys, row)) for row in response.data['report']['rows']]

    def execute(self, user, plan, expected=200):
        response = self.client_for(user).post(
            '/api/ai/report/execute', {'plan': plan}, format='json')
        self.assertEqual(response.status_code, expected, response.content)
        return response

    def test_stock_uses_flows_not_lot_qty_and_related_rows_do_not_fanout(self):
        response = self.execute(self.viewer, self.plan())
        rows = {row['lot_no']: row for row in self.rows(response)}
        self.assertEqual(set(rows), {'WH-L1', 'WH-L2', 'WH-L3'})
        expected = {
            'WH-L1': ('110', '40', '4', '74'),
            'WH-L2': ('55', '5', '0', '50'),
            'WH-L3': ('20', '2', '0', '18'),
        }
        for lot_no, values in expected.items():
            with self.subTest(lot_no=lot_no):
                row = rows[lot_no]
                self.assertEqual(row['lot_count'], 1)
                for key, value in zip(
                        ('inbound_qty', 'issued_qty', 'returned_qty', 'current_stock_qty'),
                        values):
                    self.assertEqual(Decimal(row[key]), Decimal(value))

    def test_fabric_groups_keep_units_separate(self):
        response = self.execute(self.viewer, self.plan(
            metrics=[{'key': 'current_stock_qty', 'aggregation': 'sum'}],
            dimensions=['fabric_name', 'unit'],
            order_by=[{'field': 'fabric_name', 'direction': 'asc'}]))
        rows = self.rows(response)
        self.assertEqual([(row['fabric_name'], row['unit']) for row in rows], [
            ('棉布', '米'), ('羊毛', '公斤'),
        ])
        self.assertEqual(Decimal(rows[0]['current_stock_qty']), Decimal('124'))
        self.assertEqual(Decimal(rows[1]['current_stock_qty']), Decimal('18'))

    def test_quantity_metrics_require_unit_but_lot_count_does_not(self):
        self.execute(self.viewer, self.plan(dimensions=[], order_by=[]), expected=400)
        response = self.execute(self.viewer, self.plan(
            metrics=[{'key': 'lot_count', 'aggregation': 'count'}],
            dimensions=[], order_by=[]))
        self.assertEqual(self.rows(response), [{'lot_count': 3}])

    def test_soft_deleted_lots_require_full_data_permission(self):
        plan = self.plan(
            include_deleted=True,
            filters=[{'field': 'lot_no', 'operator': 'eq', 'value': 'WH-L-DEL'}])
        response = self.execute(self.owner, plan)
        row = self.rows(response)[0]
        self.assertEqual(Decimal(row['current_stock_qty']), Decimal('500'))
        self.execute(self.viewer, plan, expected=403)

    def test_natural_question_permission_and_company_scope(self):
        response = self.client_for(self.viewer).post('/api/ai/report/query', {
            'question': '各面料当前库存', 'history': [], 'mode': 'query_plan',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['planner']['source'], 'local')
        self.assertEqual(response.data['plan']['dimensions'], ['fabric_name', 'unit'])
        self.assertNotIn('WH-OTHER', str(response.data['report']['rows']))

        viewer_keys = {item['key'] for item in
                       self.client_for(self.viewer).get('/api/ai/report/catalog').data['datasets']}
        worker_keys = {item['key'] for item in
                       self.client_for(self.worker).get('/api/ai/report/catalog').data['datasets']}
        self.assertIn('warehouse_lots', viewer_keys)
        self.assertNotIn('warehouse_lots', worker_keys)
        self.execute(self.worker, self.plan(), expected=403)
