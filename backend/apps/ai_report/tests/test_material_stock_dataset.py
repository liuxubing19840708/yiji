# -*- coding: utf-8 -*-
"""物料主单位库存：换算、业务日期、未覆盖流水、软删、权限及租户隔离。"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Post, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Style
from apps.production.models import Production
from apps.tech.models import Material
from apps.warehouse.models import (
    IssueItem, Lot, MaterialIssue, StockIn, StockInItem,
)


class MaterialStockDatasetApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.yesterday = cls.today - timedelta(days=1)
        cls.old_at = timezone.now() - timedelta(days=2)
        cls.current_at = timezone.now()
        cls.owner = User.objects.create_user(
            username='material-stock-owner', phone='13900008601', password='test-only')
        cls.company, _ = create_company_with_owner(cls.owner, '物料库存A厂')
        post = Post.objects.create(
            company=cls.company, name='物料库存岗', permissions=['warehouse.manage'])
        plain_post = Post.objects.create(company=cls.company, name='物料库存普工', permissions=[])
        cls.viewer = User.objects.create_user(
            username='material-stock-viewer', phone='13900008602', password='test-only')
        cls.worker = User.objects.create_user(
            username='material-stock-worker', phone='13900008603', password='test-only')
        UserCompany.objects.create(
            user=cls.viewer, company=cls.company, post=post,
            status=UserCompany.Status.ACTIVE)
        UserCompany.objects.create(
            user=cls.worker, company=cls.company, post=plain_post,
            status=UserCompany.Status.ACTIVE)

        cls.kg = Material.objects.create(
            company=cls.company, code='MS-KG', name='针织主料', unit='公斤',
            weight_gsm='200', width_cm='160')
        cls.metre = Material.objects.create(
            company=cls.company, code='MS-M', name='梭织主料', unit='米',
            weight_gsm='200', width_cm='160')
        cls.unknown = Material.objects.create(
            company=cls.company, code='MS-UNKNOWN', name='未知副单位料', unit='公斤',
            weight_gsm='200', width_cm='160')
        cls.missing = Material.objects.create(
            company=cls.company, code='MS-MISSING', name='待补参数料', unit='公斤')
        cls.deleted = Material.objects.create(
            company=cls.company, code='MS-DELETED', name='已删物料', unit='码',
            is_deleted=True)
        cls.materials = (cls.kg, cls.metre, cls.unknown, cls.missing, cls.deleted)
        cls.lots = {}
        for material in cls.materials:
            unit = ('米' if material == cls.kg else
                    '码' if material == cls.unknown else material.unit)
            cls.lots[material.code] = Lot.objects.create(
                company=cls.company, lot_no=f'LOT-{material.code}',
                fabric_name=material.name, unit=unit)

        style = Style.objects.create(company=cls.company, style_no='MS-S1')
        cls.production = Production.objects.create(
            company=cls.company, style=style, bed_no='MS-B1', total_qty=100)
        old_in = StockIn.objects.create(
            company=cls.company, in_no='MS-IN-OLD', in_at=cls.old_at,
            status=StockIn.Status.DONE)
        current_in = StockIn.objects.create(
            company=cls.company, in_no='MS-IN-CURRENT', in_at=cls.current_at,
            status=StockIn.Status.DRAFT)
        deleted_in = StockIn.objects.create(
            company=cls.company, in_no='MS-IN-DELETED', in_at=cls.old_at,
            status=StockIn.Status.DONE, is_deleted=True)
        StockInItem.objects.bulk_create([
            StockInItem(stock_in=old_in, lot=cls.lots['MS-KG'], material=cls.kg,
                        fabric_name=cls.kg.name, lot_no='LOT-MS-KG', qty='100', unit='公斤'),
            StockInItem(stock_in=current_in, lot=cls.lots['MS-KG'], material=cls.kg,
                        fabric_name=cls.kg.name, lot_no='LOT-MS-KG', qty='50', unit='米'),
            StockInItem(stock_in=old_in, lot=cls.lots['MS-M'], material=cls.metre,
                        fabric_name=cls.metre.name, lot_no='LOT-MS-M', qty='10', unit='kg'),
            StockInItem(stock_in=current_in, lot=cls.lots['MS-M'], material=cls.metre,
                        fabric_name=cls.metre.name, lot_no='LOT-MS-M', qty='20', unit='米'),
            StockInItem(stock_in=old_in, lot=cls.lots['MS-UNKNOWN'], material=cls.unknown,
                        fabric_name=cls.unknown.name, lot_no='LOT-MS-UNKNOWN',
                        qty='7', unit='码'),
            StockInItem(stock_in=old_in, lot=cls.lots['MS-MISSING'], material=cls.missing,
                        fabric_name=cls.missing.name, lot_no='LOT-MS-MISSING',
                        qty='8', unit='米'),
            StockInItem(stock_in=old_in, lot=cls.lots['MS-DELETED'], material=cls.deleted,
                        fabric_name=cls.deleted.name, lot_no='LOT-MS-DELETED',
                        qty='10', unit='码'),
            StockInItem(stock_in=deleted_in, lot=cls.lots['MS-KG'], material=cls.kg,
                        fabric_name=cls.kg.name, lot_no='LOT-MS-KG', qty='900', unit='公斤'),
            StockInItem(stock_in=old_in, lot=cls.lots['MS-KG'], material=cls.kg,
                        fabric_name=cls.kg.name, lot_no='LOT-MS-KG', qty='800',
                        unit='公斤', is_deleted=True),
        ])

        old_issue = MaterialIssue.objects.create(
            company=cls.company, issue_no='MS-ISSUE-OLD', type=MaterialIssue.Type.ISSUE,
            status=MaterialIssue.Status.CONFIRMED, issue_at=cls.old_at)
        current_return = MaterialIssue.objects.create(
            company=cls.company, issue_no='MS-RETURN-CURRENT', type=MaterialIssue.Type.RETURN,
            status=MaterialIssue.Status.CONFIRMED, issue_at=cls.current_at)
        draft_issue = MaterialIssue.objects.create(
            company=cls.company, issue_no='MS-ISSUE-DRAFT', type=MaterialIssue.Type.ISSUE,
            status=MaterialIssue.Status.DRAFT, issue_at=cls.old_at)
        deleted_issue = MaterialIssue.objects.create(
            company=cls.company, issue_no='MS-ISSUE-DELETED', type=MaterialIssue.Type.ISSUE,
            status=MaterialIssue.Status.CONFIRMED, issue_at=cls.old_at, is_deleted=True)
        IssueItem.objects.bulk_create([
            IssueItem(issue=old_issue, production=cls.production, lot=cls.lots['MS-KG'],
                      material=cls.kg, qty='10'),
            IssueItem(issue=old_issue, production=cls.production, lot=cls.lots['MS-M'],
                      material=cls.metre, qty='5'),
            IssueItem(issue=old_issue, production=cls.production,
                      lot=cls.lots['MS-UNKNOWN'], material=cls.unknown, qty='7'),
            IssueItem(issue=current_return, production=cls.production,
                      lot=cls.lots['MS-KG'], material=cls.kg, qty='2'),
            IssueItem(issue=draft_issue, production=cls.production, lot=cls.lots['MS-M'],
                      material=cls.metre, qty='500'),
            IssueItem(issue=deleted_issue, production=cls.production,
                      lot=cls.lots['MS-UNKNOWN'], material=cls.unknown, qty='700'),
        ])

        other_owner = User.objects.create_user(
            username='material-stock-other', phone='13900008604', password='test-only')
        other_company, _ = create_company_with_owner(other_owner, '物料库存B厂')
        other_material = Material.objects.create(
            company=other_company, code='MS-OTHER', name='他厂物料', unit='公斤')
        other_lot = Lot.objects.create(
            company=other_company, lot_no='MS-OTHER-LOT', unit='公斤')
        other_in = StockIn.objects.create(
            company=other_company, in_no='MS-OTHER-IN', in_at=cls.old_at,
            status=StockIn.Status.DONE)
        StockInItem.objects.create(
            stock_in=other_in, lot=other_lot, material=other_material,
            fabric_name='他厂物料', lot_no='MS-OTHER-LOT', qty='999', unit='公斤')

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        client.credentials(HTTP_X_COMPANY_ID=str(self.company.id))
        return client

    @staticmethod
    def plan(**patch):
        value = {
            'dataset': 'material_stock',
            'metrics': [
                {'key': 'inbound_qty', 'aggregation': 'sum'},
                {'key': 'issued_qty', 'aggregation': 'sum'},
                {'key': 'returned_qty', 'aggregation': 'sum'},
                {'key': 'current_stock_qty', 'aggregation': 'sum'},
                {'key': 'uncovered_flow_count', 'aggregation': 'count'},
            ],
            'dimensions': ['material_code', 'main_unit'],
            'filters': [],
            'order_by': [{'field': 'material_code', 'direction': 'asc'}],
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

    def test_same_unit_and_cross_unit_stock_use_current_material_parameters(self):
        response = self.execute(self.viewer, self.plan())
        rows = {row['material_code']: row for row in self.rows(response)}
        self.assertEqual(set(rows), {'MS-KG', 'MS-M', 'MS-UNKNOWN', 'MS-MISSING'})
        kg = rows['MS-KG']
        self.assertEqual(Decimal(kg['inbound_qty']), Decimal('116'))
        self.assertEqual(Decimal(kg['issued_qty']), Decimal('3.2'))
        self.assertEqual(Decimal(kg['returned_qty']), Decimal('0.64'))
        self.assertEqual(Decimal(kg['current_stock_qty']), Decimal('113.44'))
        metre = rows['MS-M']
        self.assertEqual(Decimal(metre['inbound_qty']), Decimal('51.25'))
        self.assertEqual(Decimal(metre['issued_qty']), Decimal('5'))
        self.assertEqual(Decimal(metre['current_stock_qty']), Decimal('46.25'))

    def test_unknown_unit_and_missing_parameters_are_not_fabricated(self):
        response = self.execute(self.viewer, self.plan())
        rows = {row['material_code']: row for row in self.rows(response)}
        for code in ('MS-UNKNOWN', 'MS-MISSING'):
            with self.subTest(code=code):
                self.assertEqual(Decimal(rows[code]['current_stock_qty']), Decimal('0'))
                self.assertEqual(
                    rows[code]['uncovered_flow_count'], 2 if code == 'MS-UNKNOWN' else 1)
        self.assertIn('未覆盖流水行数', response.data['report']['note'])

    def test_as_of_recomputes_current_retained_flows_and_is_serialized(self):
        response = self.execute(self.viewer, self.plan(as_of=self.yesterday.isoformat()))
        self.assertEqual(response.data['plan']['as_of'], self.yesterday.isoformat())
        rows = {row['material_code']: row for row in self.rows(response)}
        self.assertEqual(Decimal(rows['MS-KG']['inbound_qty']), Decimal('100'))
        self.assertEqual(Decimal(rows['MS-KG']['returned_qty']), Decimal('0'))
        self.assertEqual(Decimal(rows['MS-KG']['current_stock_qty']), Decimal('96.8'))
        self.assertEqual(Decimal(rows['MS-M']['inbound_qty']), Decimal('31.25'))
        self.assertEqual(Decimal(rows['MS-M']['current_stock_qty']), Decimal('26.25'))
        self.assertTrue(any('非审计快照' in item
                            for item in response.data['report']['scope']))

    def test_as_of_rejects_bad_dates_future_dates_and_unsupported_datasets(self):
        invalid_dates = [
            '20261110', '2026-02-30', 20261110, None,
            (self.today + timedelta(days=1)).isoformat(),
        ]
        for value in invalid_dates:
            with self.subTest(value=value):
                self.execute(self.viewer, self.plan(as_of=value), expected=400)
        unsupported = self.plan(as_of=self.yesterday.isoformat(), dataset='warehouse_lots')
        self.execute(self.viewer, unsupported, expected=400)
        unsupported['dataset'] = 'piecework_records'
        self.execute(self.owner, unsupported, expected=400)

    def test_soft_deleted_material_and_flows_follow_current_state(self):
        default_codes = {row['material_code'] for row in
                         self.rows(self.execute(self.owner, self.plan()))}
        self.assertNotIn('MS-DELETED', default_codes)
        included = self.execute(self.owner, self.plan(include_deleted=True))
        rows = {row['material_code']: row for row in self.rows(included)}
        self.assertEqual(Decimal(rows['MS-DELETED']['current_stock_qty']), Decimal('10'))
        self.assertEqual(Decimal(rows['MS-KG']['current_stock_qty']), Decimal('113.44'))
        self.assertEqual(rows['MS-UNKNOWN']['uncovered_flow_count'], 2)
        self.execute(self.viewer, self.plan(include_deleted=True), expected=403)

    def test_company_scope_and_dataset_permission_are_enforced(self):
        response = self.execute(self.viewer, self.plan())
        self.assertNotIn('MS-OTHER', str(response.data['report']['rows']))
        self.execute(self.worker, self.plan(), expected=403)
        viewer_catalog = self.client_for(self.viewer).get('/api/ai/report/catalog')
        worker_catalog = self.client_for(self.worker).get('/api/ai/report/catalog')
        viewer_sets = {item['key']: item for item in viewer_catalog.data['datasets']}
        self.assertTrue(viewer_sets['material_stock']['supports_as_of'])
        self.assertFalse(viewer_sets['warehouse_lots']['supports_as_of'])
        self.assertNotIn('material_stock', {
            item['key'] for item in worker_catalog.data['datasets']})

    def test_quantity_aggregation_still_requires_main_unit_dimension(self):
        self.execute(self.viewer, self.plan(dimensions=[], order_by=[]), expected=400)
        response = self.execute(self.viewer, self.plan(
            metrics=[{'key': 'uncovered_flow_count', 'aggregation': 'count'}],
            dimensions=[], order_by=[]))
        self.assertEqual(self.rows(response), [{'uncovered_flow_count': 3}])
