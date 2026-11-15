# -*- coding: utf-8 -*-
"""发布前高风险回归：多租户外键、导出隔离、批量登记逐条语义。

全部数据由 Django 测试运行器在隔离测试库中创建并回滚，不读取或清理现有业务库。
运行：.venv/Scripts/python.exe manage.py test test_release_regression -v 2
"""
from io import BytesIO

from openpyxl import load_workbook
from rest_framework.test import APIClient
from django.test import TestCase

from apps.accounts.models import User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer, Process, Style, Ticket
from apps.orders.models import Order
from apps.production.models import Production, ProductionProcess
from apps.quality.models import QualityRecord
from apps.records.models import Record
from apps.subcontract.models import OutsourceOrder, OutsourceReceive
from apps.warehouse.models import Supplier


class ReleaseRegressionTestCase(TestCase):
    """两家公司全由测试工厂创建；A 厂管理员作为 API 操作人。"""

    @classmethod
    def setUpTestData(cls):
        cls.owner_a = User.objects.create_user(
            username='release-owner-a', phone='13900001001', password='test-only')
        cls.owner_b = User.objects.create_user(
            username='release-owner-b', phone='13900001002', password='test-only')
        cls.company_a, cls.admin_post_a = create_company_with_owner(
            cls.owner_a, '发布回归A厂')
        cls.company_b, cls.admin_post_b = create_company_with_owner(
            cls.owner_b, '发布回归B厂')

        cls.style_a = cls._make_style(cls.company_a, 'RG-A')
        cls.style_b = cls._make_style(cls.company_b, 'RG-B')
        cls.production_a = cls._make_production(cls.company_a, cls.style_a, 'RG-A-BED')
        cls.production_b = cls._make_production(cls.company_b, cls.style_b, 'RG-B-BED')
        cls.ticket_a = cls._make_ticket(cls.company_a, cls.production_a, 'RG-A-QR', 10)
        cls.ticket_a2 = cls._make_ticket(cls.company_a, cls.production_a, 'RG-A-QR-2', 10, '2')
        cls.ticket_b = cls._make_ticket(cls.company_b, cls.production_b, 'RG-B-QR', 10)

        cls.process_a = Process.objects.create(
            company=cls.company_a, name='发布回归工序A', default_price='1.000', is_fixed=True)
        cls.process_b = Process.objects.create(
            company=cls.company_b, name='发布回归工序B', default_price='2.000', is_fixed=True)
        ProductionProcess.objects.create(
            production=cls.production_a, process=cls.process_a, price='1.000')
        ProductionProcess.objects.create(
            production=cls.production_b, process=cls.process_b, price='2.000')

        cls.worker_a = cls._make_member(
            cls.company_a, cls.admin_post_a, 'release-worker-a', '13900001003', 'A厂工人')
        cls.worker_b = cls._make_member(
            cls.company_b, cls.admin_post_b, 'release-worker-b', '13900001004', 'B厂工人')
        cls.supplier_a = Supplier.objects.create(
            company=cls.company_a, code='RG-SUP-A', name='A厂供应商')
        cls.supplier_b = Supplier.objects.create(
            company=cls.company_b, code='RG-SUP-B', name='B厂供应商')
        customer_b = Customer.objects.create(
            company=cls.company_b, customer_no='RG-CU-B', name='B厂客户')
        cls.order_b = Order.objects.create(
            company=cls.company_b, order_no='RG-ORDER-B', customer=customer_b,
            style=cls.style_b)

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(user=self.owner_a)
        self.api.credentials(HTTP_X_COMPANY_ID=str(self.company_a.id))

    @staticmethod
    def _make_style(company, style_no):
        return Style.objects.create(company=company, style_no=style_no, name=f'{style_no}款')

    @staticmethod
    def _make_production(company, style, bed_no):
        return Production.objects.create(
            company=company, style=style, bed_no=bed_no, total_qty=20)

    @staticmethod
    def _make_ticket(company, production, qr_code, qty, bundle_no='1'):
        return Ticket.objects.create(
            company=company, production=production, bed_no=production.bed_no,
            bundle_no=bundle_no, color='蓝', size='M', qty=qty, qr_code=qr_code)

    @staticmethod
    def _make_member(company, post, username, phone, real_name):
        user = User.objects.create_user(username=username, phone=phone, password='test-only')
        UserCompany.objects.create(
            user=user, company=company, post=post, status=UserCompany.Status.ACTIVE)
        return user

    @staticmethod
    def _xlsx_rows(response):
        workbook = load_workbook(BytesIO(response.content), data_only=True)
        return list(workbook.active.values)


class QualityTenantRegressionTests(ReleaseRegressionTestCase):
    def test_create_rejects_foreign_ticket(self):
        response = self.api.post('/api/quality/records', {
            'ticket': self.ticket_b.id, 'process': self.process_a.id,
            'defect_qty': 1, 'reason': '跨厂菲票',
        }, format='json')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(QualityRecord.objects.filter(company=self.company_a).exists())

    def test_create_rejects_foreign_process(self):
        response = self.api.post('/api/quality/records', {
            'ticket': self.ticket_a.id, 'process': self.process_b.id,
            'defect_qty': 1, 'reason': '跨厂工序',
        }, format='json')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('process', response.data)
        self.assertFalse(QualityRecord.objects.filter(company=self.company_a).exists())

    def test_create_rejects_foreign_producer(self):
        response = self.api.post('/api/quality/records', {
            'ticket': self.ticket_a.id, 'process': self.process_a.id,
            'producer': self.worker_b.id, 'defect_qty': 1, 'reason': '跨厂工人',
        }, format='json')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('producer', response.data)
        self.assertFalse(QualityRecord.objects.filter(company=self.company_a).exists())

    def test_list_and_detail_are_company_scoped(self):
        local = QualityRecord.objects.create(
            company=self.company_a, ticket=self.ticket_a, process=self.process_a,
            defect_qty=2, reason='A厂返修', inspector=self.owner_a)
        foreign = QualityRecord.objects.create(
            company=self.company_b, ticket=self.ticket_b, process=self.process_b,
            defect_qty=3, reason='B厂返修', inspector=self.owner_b)

        response = self.api.get('/api/quality/records')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['id'] for row in response.data['results']], [local.id])
        self.assertEqual(self.api.get(f'/api/quality/records/{foreign.id}').status_code, 404)
        self.assertEqual(self.api.get(f'/api/quality/records/{local.id}').status_code, 200)

    def test_export_contains_only_current_company_rows(self):
        QualityRecord.objects.create(
            company=self.company_a, ticket=self.ticket_a, process=self.process_a,
            defect_qty=2, reason='A厂导出标记', inspector=self.owner_a)
        QualityRecord.objects.create(
            company=self.company_b, ticket=self.ticket_b, process=self.process_b,
            defect_qty=3, reason='B厂导出标记', inspector=self.owner_b)

        response = self.api.get('/api/quality/export')
        self.assertEqual(response.status_code, 200)
        rows = self._xlsx_rows(response)
        self.assertEqual(len(rows), 2, rows)  # 表头 + A 厂一行
        self.assertEqual(rows[1][1], self.style_a.style_no)
        self.assertEqual(rows[1][9], 'A厂导出标记')
        self.assertNotIn('B厂导出标记', str(rows))


class OutsourceTenantRegressionTests(ReleaseRegressionTestCase):
    def test_create_rejects_foreign_supplier(self):
        response = self.api.post('/api/outsource/orders', {
            'supplier': self.supplier_b.id, 'send_qty': 5, 'unit_price': '1.000',
        }, format='json')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('supplier', response.data)
        self.assertFalse(OutsourceOrder.objects.filter(company=self.company_a).exists())

    def test_create_rejects_foreign_style(self):
        response = self.api.post('/api/outsource/orders', {
            'factory_name': '本厂外协', 'style': self.style_b.id,
            'send_qty': 5, 'unit_price': '1.000',
        }, format='json')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('style', response.data)
        self.assertFalse(OutsourceOrder.objects.filter(company=self.company_a).exists())

    def test_create_rejects_foreign_order(self):
        response = self.api.post('/api/outsource/orders', {
            'factory_name': '本厂外协', 'order': self.order_b.id,
            'send_qty': 5, 'unit_price': '1.000',
        }, format='json')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('order', response.data)
        self.assertFalse(OutsourceOrder.objects.filter(company=self.company_a).exists())

    def test_foreign_detail_and_actions_return_404(self):
        foreign = OutsourceOrder.objects.create(
            company=self.company_b, no='RG-OS-B', factory_name='B厂外协',
            send_qty=10, unit_price='1.000', amount='10.00', creator=self.owner_b)
        self.assertEqual(self.api.get(f'/api/outsource/orders/{foreign.id}').status_code, 404)
        self.assertEqual(self.api.put(
            f'/api/outsource/orders/{foreign.id}', {'action': 'receive', 'qty': 1},
            format='json').status_code, 404)
        self.assertEqual(self.api.delete(f'/api/outsource/orders/{foreign.id}').status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.received_qty, 0)
        self.assertFalse(OutsourceReceive.objects.filter(order=foreign).exists())

    def test_export_contains_only_current_company_rows(self):
        local = OutsourceOrder.objects.create(
            company=self.company_a, no='RG-OS-A', factory_name='A厂外协',
            style=self.style_a, send_qty=3, unit_price='0.333', amount='1.00',
            creator=self.owner_a, remark='A厂导出标记')
        OutsourceOrder.objects.create(
            company=self.company_b, no='RG-OS-B', factory_name='B厂外协',
            style=self.style_b, send_qty=4, unit_price='1.000', amount='4.00',
            creator=self.owner_b, remark='B厂导出标记')

        response = self.api.get('/api/outsource/export')
        self.assertEqual(response.status_code, 200)
        rows = self._xlsx_rows(response)
        self.assertEqual(len(rows), 2, rows)  # 表头 + A 厂一行
        self.assertEqual(rows[1][0], local.no)
        self.assertEqual(rows[1][8], '1.00')
        self.assertEqual(rows[1][13], 'A厂导出标记')
        self.assertNotIn('B厂导出标记', str(rows))


class BatchRegisterRegressionTests(ReleaseRegressionTestCase):
    def test_duplicate_ticket_results_keep_order_and_overlimit_reason(self):
        response = self.api.post('/api/records/batch-register', {
            'process_id': self.process_a.id,
            'entries': [
                {'ticket_id': self.ticket_a.id, 'qty': 4},
                {'ticket_id': self.ticket_a2.id, 'qty': 11},
                {'ticket_id': self.ticket_a.id, 'qty': 6},
            ],
        }, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.data['total'], response.data['ok'], response.data['fail']), (3, 2, 1))
        results = response.data['results']
        self.assertEqual([row['ticket_id'] for row in results], [
            self.ticket_a.id, self.ticket_a2.id, self.ticket_a.id])
        self.assertTrue(results[0]['ok'])
        self.assertFalse(results[1]['ok'])
        self.assertIn('超限', results[1]['error'])
        self.assertTrue(results[2]['ok'])
        self.assertNotEqual(results[0]['record_id'], None)
        self.assertEqual(results[0]['record_id'], results[2]['record_id'])

        record = Record.objects.get(
            company=self.company_a, ticket=self.ticket_a, process=self.process_a,
            user=self.owner_a)
        self.assertEqual(record.qty, 10)
        self.assertFalse(Record.objects.filter(ticket=self.ticket_a2).exists())

    def test_duplicate_entries_cannot_exceed_ticket_quantity(self):
        response = self.api.post('/api/records/batch-register', {
            'process_id': self.process_a.id,
            'entries': [
                {'ticket_id': self.ticket_a.id, 'qty': 7},
                {'ticket_id': self.ticket_a.id, 'qty': 4},
            ],
        }, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.data['ok'], response.data['fail']), (1, 1))
        self.assertTrue(response.data['results'][0]['ok'])
        self.assertFalse(response.data['results'][1]['ok'])
        self.assertIn('超限', response.data['results'][1]['error'])
        record = Record.objects.get(
            company=self.company_a, ticket=self.ticket_a, process=self.process_a,
            user=self.owner_a)
        self.assertEqual(record.qty, 7)
